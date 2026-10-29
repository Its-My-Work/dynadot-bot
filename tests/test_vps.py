"""Tests for the VPS/VPN module: Xray helpers, providers, and API routes.

The provider adapters are driven through ``httpx.MockTransport`` rather than a
mocked-out client, so the request bodies we actually put on the wire (and the
nesting of the responses we parse) are both covered.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid as uuid_module
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.deps import get_dynadot_client, get_payment_gateway, get_vps_provider
from app.integrations.payments.exceptions import PaymentError
from app.integrations.vps.aeza import AezaVpsProvider
from app.integrations.vps.base import (
    BaseVpsProvider,
    VpsApiError,
    VpsConfigError,
    VpsTransportError,
)
from app.integrations.vps.catalog import DEFAULT_LOCATIONS, DEFAULT_PRESETS
from app.integrations.vps.mock import MockVpsProvider
from app.integrations.vps.pqhosting import PqHostingVpsProvider
from app.integrations.vps.timeweb import TimewebVpsProvider
from app.integrations.vps.xray import (
    DEFAULT_SNI,
    VLESS_PORT,
    build_cloud_init,
    build_vless_uri,
    build_xray_config,
    generate_client_uuid,
    generate_credentials,
    generate_short_id,
    generate_x25519_keypair,
)
from app.main import create_app
from app.models import Base
from app.models.enums import ServerStatus, VpsProvider
from app.models.user import User
from app.models.vps import ServerInstance
from tests.test_registration_service import FakeDynadot

TELEGRAM_ID = 555000222


# ============================================================ xray key material


def test_x25519_keypair_is_32_bytes_base64url():
    """Xray only accepts raw 32-byte scalars in unpadded base64url."""
    keys = generate_x25519_keypair()
    for value in (keys.private_key, keys.public_key):
        assert "=" not in value
        assert not {"+", "/"} & set(value)
        assert len(base64.urlsafe_b64decode(value + "==")) == 32


def test_keypairs_are_unique_per_call():
    assert generate_x25519_keypair().private_key != generate_x25519_keypair().private_key


def test_client_uuid_is_a_valid_uuid4():
    value = generate_client_uuid()
    assert uuid_module.UUID(value).version == 4


def test_short_id_is_eight_hex_chars():
    short_id = generate_short_id()
    assert len(short_id) == 8
    assert re.fullmatch(r"[0-9a-f]{8}", short_id)


def test_short_id_length_is_configurable():
    assert len(generate_short_id(16)) == 16
    assert len(generate_short_id(4)) == 4


@pytest.mark.parametrize("bad", [0, 7, 18, -2])
def test_short_id_rejects_invalid_lengths(bad):
    """Reality requires an even-length hex id of at most 16 chars."""
    with pytest.raises(ValueError):
        generate_short_id(bad)


def test_generate_credentials_bundles_everything():
    creds = generate_credentials("www.example.com")
    assert creds.sni == "www.example.com"
    assert uuid_module.UUID(creds.uuid)
    assert creds.private_key and creds.public_key
    assert len(creds.short_id) == 8


# ================================================================ xray config


def test_xray_config_is_vless_reality_on_443():
    config = build_xray_config(
        client_uuid="abc-uuid", private_key="PRIV", short_id="deadbeef"
    )
    inbound = config["inbounds"][0]
    assert inbound["port"] == 443
    assert inbound["protocol"] == "vless"
    assert inbound["settings"]["decryption"] == "none"
    assert inbound["settings"]["clients"][0] == {
        "id": "abc-uuid",
        "flow": "xtls-rprx-vision",
    }

    stream = inbound["streamSettings"]
    assert stream["network"] == "tcp"
    assert stream["security"] == "reality"
    reality = stream["realitySettings"]
    assert reality["privateKey"] == "PRIV"
    assert reality["shortIds"] == ["deadbeef"]
    assert reality["serverNames"] == [DEFAULT_SNI]
    assert reality["dest"] == f"{DEFAULT_SNI}:443"


def test_xray_config_blocks_private_ranges():
    """A customer must not be able to reach the provider's internal network."""
    config = build_xray_config(
        client_uuid="u", private_key="p", short_id="s", sni="www.test.com"
    )
    rules = config["routing"]["rules"]
    private = next(r for r in rules if "geoip:private" in (r.get("ip") or []))
    assert private["outboundTag"] == "block"


# ============================================================== cloud-init


@pytest.fixture
def cloud_init_doc():
    return build_cloud_init(
        client_uuid="11111111-2222-3333-4444-555555555555",
        private_key="PRIVKEY123",
        short_id="a1b2c3d4",
        sni="www.microsoft.com",
        hostname="vpn-nl-start",
    )


def test_cloud_init_starts_with_the_cloud_config_header(cloud_init_doc):
    """cloud-init ignores user-data that does not open with #cloud-config."""
    assert cloud_init_doc.startswith("#cloud-config")


def test_cloud_init_is_valid_yaml(cloud_init_doc):
    parsed = yaml.safe_load(cloud_init_doc)
    assert isinstance(parsed, dict)
    assert parsed["package_update"] is True
    assert parsed["hostname"] == "vpn-nl-start"
    assert "curl" in parsed["packages"]


def test_cloud_init_embeds_a_parseable_xray_config(cloud_init_doc):
    """The heredoc indentation must survive into valid JSON on the VM."""
    parsed = yaml.safe_load(cloud_init_doc)
    config_file = next(
        f
        for f in parsed["write_files"]
        if f["path"] == "/usr/local/etc/xray/config.json"
    )
    config = json.loads(config_file["content"])
    reality = config["inbounds"][0]["streamSettings"]["realitySettings"]
    assert reality["privateKey"] == "PRIVKEY123"
    assert reality["shortIds"] == ["a1b2c3d4"]
    assert config["inbounds"][0]["settings"]["clients"][0]["id"].startswith("11111111")


def test_cloud_init_installs_official_xray_release(cloud_init_doc):
    parsed = yaml.safe_load(cloud_init_doc)
    script = next(
        f for f in parsed["write_files"] if f["path"] == "/usr/local/bin/install-xray.sh"
    )["content"]
    assert "XTLS/Xray-install" in script
    assert "systemctl enable xray" in script
    assert "ufw allow 443/tcp" in script


def test_cloud_init_enables_bbr(cloud_init_doc):
    """CUBIC costs real throughput on a long RU<->EU path."""
    parsed = yaml.safe_load(cloud_init_doc)
    sysctl = next(
        f for f in parsed["write_files"] if f["path"] == "/etc/sysctl.d/99-xray.conf"
    )["content"]
    assert "tcp_congestion_control=bbr" in sysctl


def test_cloud_init_runs_the_installer(cloud_init_doc):
    parsed = yaml.safe_load(cloud_init_doc)
    assert any("install-xray.sh" in str(cmd) for cmd in parsed["runcmd"])


def test_cloud_init_omits_hostname_when_not_given():
    doc = build_cloud_init(client_uuid="u", private_key="p", short_id="s")
    assert "hostname" not in yaml.safe_load(doc)


# ================================================================ vless link


def test_vless_uri_has_the_exact_expected_shape():
    uri = build_vless_uri(
        client_uuid="UUID1",
        ip_address="203.0.113.7",
        public_key="PUBKEY",
        short_id="a1b2c3d4",
        sni="www.microsoft.com",
        name="NL-Start",
    )
    assert uri == (
        "vless://UUID1@203.0.113.7:443?security=reality&encryption=none"
        "&pbk=PUBKEY&headerType=none&fp=chrome&type=tcp&flow=xtls-rprx-vision"
        "&sni=www.microsoft.com&sid=a1b2c3d4#NL-Start"
    )


def test_vless_uri_parses_into_the_right_parameters():
    uri = build_vless_uri(
        client_uuid="u1",
        ip_address="1.2.3.4",
        public_key="pk",
        short_id="sid12345",
        name="Server",
    )
    parsed = urlparse(uri)
    assert parsed.scheme == "vless"
    assert parsed.username == "u1"
    assert parsed.hostname == "1.2.3.4"
    assert parsed.port == VLESS_PORT

    query = parse_qs(parsed.query)
    assert query["security"] == ["reality"]
    assert query["flow"] == ["xtls-rprx-vision"]
    assert query["fp"] == ["chrome"]
    assert query["type"] == ["tcp"]
    assert query["encryption"] == ["none"]
    assert query["headerType"] == ["none"]
    assert query["pbk"] == ["pk"]
    assert query["sid"] == ["sid12345"]


def test_vless_uri_percent_encodes_the_name():
    """Cyrillic server names must survive a QR-code round trip."""
    uri = build_vless_uri(
        client_uuid="u",
        ip_address="1.1.1.1",
        public_key="k",
        short_id="s",
        name="VPN Нидерланды",
    )
    fragment = uri.split("#", 1)[1]
    assert " " not in fragment
    assert "%" in fragment


# ============================================================== mock provider


def test_mock_provider_serves_the_full_catalogue():
    provider = MockVpsProvider()
    locations = asyncio.run(provider.list_locations())
    presets = asyncio.run(provider.list_presets())
    assert [loc.code for loc in locations] == ["nl", "pl", "kz", "tr", "fi"]
    assert [p.price_rub for p in presets] == [790.0, 1290.0, 2490.0]


def test_mock_provider_activates_on_first_poll():
    """Mirrors a real VM: no IP at create, address once cloud-init finishes."""
    provider = MockVpsProvider()
    created = asyncio.run(
        provider.create_server("nl", "start", "#cloud-config\n", "vpn-1")
    )
    assert created.status is ServerStatus.INSTALLING
    assert created.ip_address is None

    polled = asyncio.run(provider.get_server(created.external_id))
    assert polled.status is ServerStatus.ACTIVE
    assert polled.ip_address


def test_mock_provider_can_stay_installing():
    provider = MockVpsProvider(auto_activate=False)
    created = asyncio.run(provider.create_server("nl", "pro", "x", "vpn"))
    polled = asyncio.run(provider.get_server(created.external_id))
    assert polled.status is ServerStatus.INSTALLING


def test_mock_provider_records_the_cloud_init_it_was_given():
    provider = MockVpsProvider()
    created = asyncio.run(
        provider.create_server("pl", "standard", "#cloud-config\nfoo: 1", "vpn")
    )
    assert provider.cloud_init_for(created.external_id).startswith("#cloud-config")


def test_mock_provider_rejects_unknown_preset():
    provider = MockVpsProvider()
    with pytest.raises(VpsApiError):
        asyncio.run(provider.create_server("nl", "nope", "x", "vpn"))


def test_mock_provider_delete_is_idempotent():
    provider = MockVpsProvider()
    created = asyncio.run(provider.create_server("nl", "start", "x", "vpn"))
    assert asyncio.run(provider.delete_server(created.external_id)) is True
    assert asyncio.run(provider.delete_server(created.external_id)) is False


def test_mock_provider_get_missing_server_raises_404():
    provider = MockVpsProvider()
    with pytest.raises(VpsApiError) as exc:
        asyncio.run(provider.get_server("mock-nothing"))
    assert exc.value.status_code == 404


# =========================================================== provider contract


@pytest.mark.parametrize(
    "cls",
    [MockVpsProvider, TimewebVpsProvider, AezaVpsProvider, PqHostingVpsProvider],
)
def test_every_provider_implements_the_base_contract(cls):
    assert issubclass(cls, BaseVpsProvider)
    for method in (
        "list_locations",
        "list_presets",
        "create_server",
        "get_server",
        "delete_server",
    ):
        assert callable(getattr(cls, method))


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        (MockVpsProvider, VpsProvider.MOCK),
        (TimewebVpsProvider, VpsProvider.TIMEWEB),
        (AezaVpsProvider, VpsProvider.AEZA),
        (PqHostingVpsProvider, VpsProvider.PQ_HOSTING),
    ],
)
def test_providers_declare_their_enum(cls, expected):
    assert cls.provider is expected


@pytest.mark.parametrize(
    "cls", [TimewebVpsProvider, AezaVpsProvider, PqHostingVpsProvider]
)
def test_http_providers_refuse_to_run_without_a_token(cls):
    provider = cls(api_token=None)
    with pytest.raises(VpsConfigError):
        asyncio.run(provider.create_server("nl", "start", "x", "vpn"))


# ============================================================ timeweb adapter


def _transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_timeweb_create_server_sends_configurator_and_cloud_init():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/os/servers"):
            return httpx.Response(
                200,
                json={
                    "servers_os": [
                        {"id": 79, "name": "ubuntu", "version": "22.04"},
                        {"id": 12, "name": "debian", "version": "12"},
                    ]
                },
            )
        if path.endswith("/configurator/servers"):
            return httpx.Response(
                200,
                json={
                    "server_configurators": [
                        {"id": 11, "availability_zones": ["spb-1"]},
                        {"id": 42, "availability_zones": ["ams-1"]},
                    ]
                },
            )
        if path.endswith("/servers") and request.method == "POST":
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(
                200,
                json={
                    "server": {
                        "id": 900100,
                        "name": "vpn-nl-start",
                        "status": "installing",
                        "availability_zone": "ams-1",
                        "configuration": {"cpu": 1, "ram": 1024, "disk": 15360},
                        "networks": [],
                    }
                },
            )
        return httpx.Response(404, json={})

    provider = TimewebVpsProvider(api_token="tw-key", client=_transport(handler))
    server = asyncio.run(
        provider.create_server("nl", "start", "#cloud-config\nfoo: 1", "vpn-nl-start")
    )

    assert seen["auth"] == "Bearer tw-key"
    body = seen["body"]
    assert body["availability_zone"] == "ams-1"
    assert body["os_id"] == 79
    # The configurator must cover the chosen zone or Timeweb rejects the create.
    assert body["configuration"]["configurator_id"] == 42
    assert body["configuration"]["cpu"] == 1
    assert body["configuration"]["ram"] == 1024
    assert body["configuration"]["disk"] == 15360
    assert body["cloud_init"].startswith("#cloud-config")

    assert server.external_id == "900100"
    assert server.status is ServerStatus.INSTALLING
    assert server.location_code == "nl"
    assert server.location_name == "Amsterdam"
    assert server.disk_gb == 15


def test_timeweb_get_server_extracts_ipv4_and_maps_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "server": {
                    "id": 900100,
                    "name": "vpn",
                    "status": "on",
                    "availability_zone": "waw-1",
                    "configuration": {"cpu": 2, "ram": 2048, "disk": 30720},
                    "networks": [
                        {"type": "ipv6", "ip": "2a01:230::1"},
                        {"type": "ipv4", "ip": "185.22.33.44"},
                    ],
                }
            },
        )

    provider = TimewebVpsProvider(api_token="k", client=_transport(handler))
    server = asyncio.run(provider.get_server("900100"))
    assert server.status is ServerStatus.ACTIVE
    assert server.ip_address == "185.22.33.44"
    assert server.location_code == "pl"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("on", ServerStatus.ACTIVE),
        ("installing", ServerStatus.INSTALLING),
        ("no_paid", ServerStatus.SUSPENDED),
        ("deleted", ServerStatus.DELETED),
        ("error", ServerStatus.FAILED),
        ("something-new", ServerStatus.PENDING),
    ],
)
def test_timeweb_status_mapping(raw, expected):
    from app.integrations.vps.timeweb import map_status

    assert map_status(raw) is expected


def test_timeweb_delete_returns_false_when_already_gone():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    provider = TimewebVpsProvider(api_token="k", client=_transport(handler))
    assert asyncio.run(provider.delete_server("nope")) is False


def test_timeweb_delete_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    provider = TimewebVpsProvider(api_token="k", client=_transport(handler))
    assert asyncio.run(provider.delete_server("900100")) is True


def test_timeweb_rejects_unsupported_location():
    provider = TimewebVpsProvider(api_token="k", client=_transport(lambda r: None))
    with pytest.raises(VpsApiError):
        asyncio.run(provider.create_server("us", "start", "x", "vpn"))


def test_timeweb_locations_fall_back_when_api_errors():
    """Browsing plans must never 500 just because the upstream is down."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = TimewebVpsProvider(api_token="k", client=_transport(handler))
    locations = asyncio.run(provider.list_locations())
    assert len(locations) == len(DEFAULT_LOCATIONS)


def test_timeweb_transport_error_is_wrapped():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    provider = TimewebVpsProvider(api_token="k", client=_transport(handler))
    with pytest.raises(VpsTransportError):
        asyncio.run(provider.get_server("1"))


# =============================================================== aeza adapter


def test_aeza_create_server_base64_encodes_cloud_init():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/services/products"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {"id": 501, "ram": 1024, "cpu": 1, "location": "nl-ams"},
                            {"id": 502, "ram": 2048, "cpu": 2, "location": "nl-ams"},
                        ]
                    }
                },
            )
        if request.url.path.endswith("/services/orders"):
            seen["body"] = json.loads(request.content)
            seen["key"] = request.headers.get("X-API-Key")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {
                                "id": 77001,
                                "name": "vpn-nl",
                                "status": "created",
                                "location": "nl-ams",
                                "ip": {"ipv4": []},
                                "params": {"cpu": 1, "ram": 1024, "disk": 15360},
                            }
                        ]
                    }
                },
            )
        return httpx.Response(404, json={})

    provider = AezaVpsProvider(api_token="aeza-key", client=_transport(handler))
    server = asyncio.run(
        provider.create_server("nl", "start", "#cloud-config\nfoo: 1", "vpn-nl")
    )

    # Aeza uses X-API-Key, never a bearer token.
    assert seen["key"] == "aeza-key"
    body = seen["body"]
    assert body["location"] == "nl-ams"
    assert body["productId"] == 501
    assert body["os"] == "ubuntu-22.04"
    decoded = base64.b64decode(body["cloudInit"]).decode()
    assert decoded.startswith("#cloud-config")

    assert server.external_id == "77001"
    assert server.status is ServerStatus.INSTALLING
    assert server.location_code == "nl"
    assert server.disk_gb == 15


def test_aeza_in_body_error_is_raised():
    """Aeza reports failures as HTTP 200 with an `error` key."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "insufficient funds"}})

    provider = AezaVpsProvider(api_token="k", client=_transport(handler))
    with pytest.raises(VpsApiError) as exc:
        asyncio.run(provider.get_server("1"))
    assert "insufficient funds" in str(exc.value)


def test_aeza_get_server_unwraps_data_and_reads_ip():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            "id": 77001,
                            "name": "vpn",
                            "status": "active",
                            "location": "tr-ist",
                            "ip": {"ipv4": ["45.66.77.88"]},
                            "params": {"cpu": 4, "ram": 4096, "disk": 61440},
                        }
                    ]
                }
            },
        )

    provider = AezaVpsProvider(api_token="k", client=_transport(handler))
    server = asyncio.run(provider.get_server("77001"))
    assert server.status is ServerStatus.ACTIVE
    assert server.ip_address == "45.66.77.88"
    assert server.location_code == "tr"
    assert server.location_name == "Istanbul"
    assert server.disk_gb == 60


def test_aeza_presets_keep_retail_prices():
    """Upstream wholesale prices must never leak into what we charge."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"items": [{"id": 9, "ram": 2048, "price": 111}]}},
        )

    provider = AezaVpsProvider(api_token="k", client=_transport(handler))
    presets = asyncio.run(provider.list_presets())
    standard = next(p for p in presets if p.code == "standard")
    assert standard.price_rub == 1290.0
    assert standard.external_id == "9"


def test_aeza_delete_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"items": [{"id": 1}]}})

    provider = AezaVpsProvider(api_token="k", client=_transport(handler))
    assert asyncio.run(provider.delete_server("77001")) is True


# ========================================================== pq.hosting adapter


def test_pqhosting_create_server_sends_plain_user_data():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/plans"):
            return httpx.Response(
                200,
                json={"data": [{"id": "vps-2", "ram": 2048, "cpu": 2}]},
            )
        if request.url.path.endswith("/vps") and request.method == "POST":
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "pq-5521",
                        "hostname": "vpn-kz",
                        "status": "install",
                        "location": "almaty",
                        "ipv4": "92.38.1.2",
                        "cpu": 2,
                        "ram": 2048,
                        "disk": 30,
                    }
                },
            )
        return httpx.Response(404, json={})

    provider = PqHostingVpsProvider(api_token="pq-key", client=_transport(handler))
    server = asyncio.run(
        provider.create_server("kz", "standard", "#cloud-config\nfoo: 1", "vpn-kz")
    )

    body = seen["body"]
    assert body["location"] == "almaty"
    assert body["plan"] == "vps-2"
    assert body["os"] == "ubuntu-24.04"
    # PQ.Hosting decodes nothing: base64 here would reach cloud-init verbatim.
    assert body["user_data"].startswith("#cloud-config")

    assert server.external_id == "pq-5521"
    assert server.status is ServerStatus.INSTALLING
    assert server.ip_address == "92.38.1.2"
    assert server.location_code == "kz"
    assert server.location_name == "Almaty"
    assert server.disk_gb == 30


def test_pqhosting_get_and_delete():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "pq-1",
                    "hostname": "vpn",
                    "status": "active",
                    "location": "helsinki",
                    "ip": "1.2.3.4",
                }
            },
        )

    provider = PqHostingVpsProvider(api_token="k", client=_transport(handler))
    server = asyncio.run(provider.get_server("pq-1"))
    assert server.status is ServerStatus.ACTIVE
    assert server.location_code == "fi"
    assert asyncio.run(provider.delete_server("pq-1")) is True


# ============================================================ provider factory


def test_build_provider_falls_back_to_mock_without_keys():
    """An unconfigured deploy must still serve the catalogue."""
    from app.core.config import Settings
    from app.integrations.vps import build_provider

    settings = Settings(vps_provider="timeweb", timeweb_api_token=None)
    assert isinstance(build_provider(settings), MockVpsProvider)


def test_build_provider_returns_the_configured_provider():
    from app.core.config import Settings
    from app.integrations.vps import build_provider

    settings = Settings(vps_provider="aeza", aeza_api_token="secret")
    provider = build_provider(settings)
    assert isinstance(provider, AezaVpsProvider)
    assert provider.provider is VpsProvider.AEZA


def test_settings_report_vps_configuration_state():
    from app.core.config import Settings

    assert Settings(vps_provider="mock").vps_configured is False
    assert Settings(vps_provider="pq_hosting").vps_configured is False
    assert (
        Settings(vps_provider="pq_hosting", pq_hosting_api_token="t").vps_configured
        is True
    )


# ============================================================== API integration


@pytest.fixture
def vps_provider():
    return MockVpsProvider()


class FakeGateway:
    """Minimal payment gateway returning a fixed checkout URL."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.intents: list = []

    async def create_payment(self, intent):
        if self.fail:
            raise PaymentError("gateway down")
        self.intents.append(intent)
        from app.integrations.payments.base import PaymentSession
        from app.models.enums import PaymentStatus

        return PaymentSession(
            external_id=f"tx-{intent.order_id[:8]}",
            payment_url="https://pay.example/redirect",
            status=PaymentStatus.PENDING,
            amount=intent.amount,
        )


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def client(tmp_path, monkeypatch, vps_provider, gateway):
    db_path = tmp_path / "vps.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import importlib

    import app.core.config as config_module
    import app.db.session as session_module

    config_module.get_settings.cache_clear()
    new_settings = config_module.Settings()
    monkeypatch.setattr(config_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)
    monkeypatch.setattr(
        importlib.import_module("app.api.routes.vps"),
        "settings",
        new_settings,
        raising=False,
    )

    async def setup():
        async with session_module.get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = session_module.get_session_factory()
        async with factory() as session:
            session.add(User(telegram_id=TELEGRAM_ID, username="vpnbuyer"))
            await session.commit()

    asyncio.run(setup())

    app = create_app()
    app.dependency_overrides[get_dynadot_client] = lambda: FakeDynadot()
    app.dependency_overrides[get_vps_provider] = lambda: vps_provider
    app.dependency_overrides[get_payment_gateway] = lambda: gateway
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_locations_endpoint_returns_flagged_locations(client):
    response = client.get("/api/v1/servers/locations")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 5

    by_code = {item["code"]: item for item in body}
    assert by_code["nl"]["flag"] == "🇳🇱"
    assert by_code["pl"]["flag"] == "🇵🇱"
    assert by_code["kz"]["flag"] == "🇰🇿"
    assert by_code["tr"]["flag"] == "🇹🇷"
    assert by_code["fi"]["flag"] == "🇫🇮"
    assert by_code["nl"]["label"] == "🇳🇱 Amsterdam"


def test_presets_endpoint_returns_retail_prices(client):
    response = client.get("/api/v1/servers/presets")
    assert response.status_code == 200
    body = response.json()
    prices = {item["name"]: item["price_rub"] for item in body}
    assert prices == {"Start": 790.0, "Standard": 1290.0, "Pro": 2490.0}

    start = next(item for item in body if item["code"] == "start")
    assert start["cpu"] == 1
    assert start["ram_mb"] == 1024
    assert start["disk_gb"] == 15


def test_order_creates_server_and_returns_checkout_url(client, gateway):
    response = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "standard"},
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 201
    body = response.json()

    assert body["payment_url"] == "https://pay.example/redirect"
    assert body["amount"] == 1290.0
    assert body["currency"] == "RUB"

    server = body["server"]
    assert server["status"] == "pending"
    assert server["location_name"] == "Amsterdam"
    assert server["preset_name"] == "Standard"
    assert server["price_rub"] == 1290.0
    # Keys exist before the VM does, so the link can be shown immediately.
    assert uuid_module.UUID(server["vless_uuid"])
    assert server["vless_sni"]

    # The gateway was asked for exactly the frozen amount.
    assert gateway.intents[0].amount == 1290.0


def test_order_rejects_unknown_preset(client):
    response = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "platinum"},
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 400


def test_order_rejects_unknown_location(client):
    response = client.post(
        "/api/v1/servers/orders",
        json={"location": "mars", "preset": "start"},
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 400


def test_order_requires_a_known_user(client):
    response = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "start"},
        params={"telegram_id": 999999999},
    )
    assert response.status_code == 404


def test_order_provisions_directly_when_the_gateway_is_down(
    tmp_path, monkeypatch, vps_provider
):
    """A gateway outage must not block provisioning in dev / manual settlement."""
    import importlib

    import app.core.config as config_module
    import app.db.session as session_module

    db_path = tmp_path / "vps2.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    config_module.get_settings.cache_clear()
    new_settings = config_module.Settings()
    monkeypatch.setattr(config_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)
    monkeypatch.setattr(
        importlib.import_module("app.api.routes.vps"),
        "settings",
        new_settings,
        raising=False,
    )

    async def setup():
        async with session_module.get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = session_module.get_session_factory()
        async with factory() as session:
            session.add(User(telegram_id=TELEGRAM_ID, username="u"))
            await session.commit()

    asyncio.run(setup())

    app = create_app()
    app.dependency_overrides[get_dynadot_client] = lambda: FakeDynadot()
    app.dependency_overrides[get_vps_provider] = lambda: vps_provider
    app.dependency_overrides[get_payment_gateway] = lambda: FakeGateway(fail=True)

    with TestClient(app) as c:
        response = c.post(
            "/api/v1/servers/orders",
            json={"location": "fi", "preset": "pro"},
            params={"telegram_id": TELEGRAM_ID},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["payment_url"] is None
    # Provisioned immediately: external id assigned, status moved on.
    assert body["server"]["external_id"]
    assert body["server"]["status"] == "installing"


def test_my_servers_syncs_ip_and_builds_the_vless_link(client):
    """The IP arrives after create, and the link is only buildable with it."""
    created = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "start", "name": "my-vpn"},
        params={"telegram_id": TELEGRAM_ID},
    ).json()["server"]
    assert created["ip_address"] is None
    assert created["vless_link"] is None

    # Provision it (payment would normally do this), then list.
    import app.db.session as session_module
    from app.core.config import settings as live_settings
    from app.services.servers import ServerService

    async def provision():
        factory = session_module.get_session_factory()
        async with factory() as session:
            server = await session.get(ServerInstance, created["id"])
            service = ServerService(
                session, client.app.dependency_overrides[get_vps_provider](),
                live_settings,
            )
            await service.provision(server)
            await session.commit()

    asyncio.run(provision())

    response = client.get("/api/v1/servers/my", params={"telegram_id": TELEGRAM_ID})
    assert response.status_code == 200
    servers = response.json()
    assert len(servers) == 1

    server = servers[0]
    assert server["status"] == "active"
    assert server["ip_address"]
    assert server["expires_at"]

    link = server["vless_link"]
    assert link.startswith(f"vless://{server['vless_uuid']}@{server['ip_address']}:443")
    assert "security=reality" in link
    assert "flow=xtls-rprx-vision" in link
    assert "my-vpn" in link


def test_get_single_server_scoped_to_its_owner(client):
    created = client.post(
        "/api/v1/servers/orders",
        json={"location": "pl", "preset": "start"},
        params={"telegram_id": TELEGRAM_ID},
    ).json()["server"]

    ok = client.get(
        f"/api/v1/servers/{created['id']}", params={"telegram_id": TELEGRAM_ID}
    )
    assert ok.status_code == 200
    assert ok.json()["id"] == created["id"]

    missing = client.get(
        "/api/v1/servers/00000000-0000-0000-0000-000000000000",
        params={"telegram_id": TELEGRAM_ID},
    )
    assert missing.status_code == 404


def test_delete_server_marks_it_deleted_and_hides_it(client, vps_provider):
    created = client.post(
        "/api/v1/servers/orders",
        json={"location": "tr", "preset": "standard"},
        params={"telegram_id": TELEGRAM_ID},
    ).json()["server"]

    response = client.post(
        f"/api/v1/servers/{created['id']}/delete",
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    listed = client.get("/api/v1/servers/my", params={"telegram_id": TELEGRAM_ID})
    assert listed.json() == []

    with_deleted = client.get(
        "/api/v1/servers/my",
        params={"telegram_id": TELEGRAM_ID, "include_deleted": True},
    )
    assert len(with_deleted.json()) == 1


def test_delete_is_idempotent_over_the_api(client):
    created = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "start"},
        params={"telegram_id": TELEGRAM_ID},
    ).json()["server"]

    first = client.post(
        f"/api/v1/servers/{created['id']}/delete",
        params={"telegram_id": TELEGRAM_ID},
    )
    second = client.post(
        f"/api/v1/servers/{created['id']}/delete",
        params={"telegram_id": TELEGRAM_ID},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "deleted"


def test_servers_are_isolated_between_users(client):
    """One customer must never see or delete another's server."""
    import app.db.session as session_module

    other_telegram_id = TELEGRAM_ID + 1

    async def add_user():
        factory = session_module.get_session_factory()
        async with factory() as session:
            session.add(User(telegram_id=other_telegram_id, username="other"))
            await session.commit()

    asyncio.run(add_user())

    created = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "start"},
        params={"telegram_id": TELEGRAM_ID},
    ).json()["server"]

    assert client.get(
        "/api/v1/servers/my", params={"telegram_id": other_telegram_id}
    ).json() == []
    assert (
        client.get(
            f"/api/v1/servers/{created['id']}",
            params={"telegram_id": other_telegram_id},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/servers/{created['id']}/delete",
            params={"telegram_id": other_telegram_id},
        ).status_code
        == 404
    )


def test_blocked_user_cannot_order(client):
    from sqlalchemy import select

    import app.db.session as session_module

    async def block():
        factory = session_module.get_session_factory()
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == TELEGRAM_ID)
            )
            user.is_blocked = True
            await session.commit()

    asyncio.run(block())

    response = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "start"},
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 403


# ================================================================ service layer


def test_service_never_persists_the_reality_private_key(client):
    """A dump of this table must not let anyone impersonate a server."""
    created = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "start"},
        params={"telegram_id": TELEGRAM_ID},
    ).json()["server"]

    columns = {c.name for c in ServerInstance.__table__.columns}
    assert "vless_private_key" not in columns
    assert set(created) & {"vless_private_key"} == set()


def test_provision_sends_the_generated_keys_in_cloud_init(client, vps_provider):
    """The private key must reach the VM, and only via cloud-init."""
    import app.db.session as session_module
    from app.core.config import settings as live_settings
    from app.services.servers import ServerService

    created = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "start"},
        params={"telegram_id": TELEGRAM_ID},
    ).json()["server"]

    async def provision():
        factory = session_module.get_session_factory()
        async with factory() as session:
            server = await session.get(ServerInstance, created["id"])
            service = ServerService(session, vps_provider, live_settings)
            await service.provision(server)
            await session.commit()
            return server.external_id, server.vless_short_id

    external_id, short_id = asyncio.run(provision())
    user_data = vps_provider.cloud_init_for(external_id)
    parsed = yaml.safe_load(user_data)
    config = json.loads(
        next(
            f
            for f in parsed["write_files"]
            if f["path"] == "/usr/local/etc/xray/config.json"
        )["content"]
    )
    reality = config["inbounds"][0]["streamSettings"]["realitySettings"]
    assert reality["shortIds"] == [short_id]
    assert len(base64.urlsafe_b64decode(reality["privateKey"] + "==")) == 32
    assert config["inbounds"][0]["settings"]["clients"][0]["id"] == created["vless_uuid"]


def test_provision_failure_marks_the_row_failed(client):
    """A paid order must survive a provider outage as an auditable row."""
    import app.db.session as session_module
    from app.core.config import settings as live_settings
    from app.integrations.vps.base import VpsProviderError
    from app.services.servers import ServerService

    created = client.post(
        "/api/v1/servers/orders",
        json={"location": "nl", "preset": "start"},
        params={"telegram_id": TELEGRAM_ID},
    ).json()["server"]

    class BrokenProvider(MockVpsProvider):
        async def create_server(self, location, preset, cloud_init, name):
            raise VpsApiError("capacity exhausted")

    async def provision():
        factory = session_module.get_session_factory()
        async with factory() as session:
            server = await session.get(ServerInstance, created["id"])
            service = ServerService(session, BrokenProvider(), live_settings)
            with pytest.raises(VpsProviderError):
                await service.provision(server)
            await session.commit()
            return server.status

    assert asyncio.run(provision()) is ServerStatus.FAILED


def test_catalogue_constants_match_the_advertised_offering():
    assert [p.code for p in DEFAULT_PRESETS] == ["start", "standard", "pro"]
    assert [loc.country for loc in DEFAULT_LOCATIONS] == ["NL", "PL", "KZ", "TR", "FI"]
