"""Xray VLESS-Reality key material, cloud-init and share-link generation.

Reality needs three secrets generated *before* the server exists: the x25519
keypair (the private half goes into the server config, the public half into the
client link), a client UUID and a short id. Generating them here — rather than
reading them back off the box after boot — means the customer's connection
string can be shown the moment the order is paid, and provisioning stays
fire-and-forget: we never have to SSH in to collect anything.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid as uuid_module
from dataclasses import dataclass
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

#: Default SNI the Reality handshake masquerades as. It must be a high-traffic
#: TLS 1.3 host that supports x25519 and HTTP/2, otherwise the forged
#: handshake is trivially distinguishable from the real site.
DEFAULT_SNI = "www.microsoft.com"

#: SNI choices offered to the order form.
SNI_POOL = (
    "www.microsoft.com",
    "www.cloudflare.com",
    "www.samsung.com",
    "www.icloud.com",
    "www.nvidia.com",
)

#: Official installer (XTLS/Xray-install) — pinned to the raw GitHub URL the
#: project documents.
XRAY_INSTALL_URL = "https://github.com/XTLS/Xray-install/raw/main/install-release.sh"

#: Reality is only worth deploying on 443: any other port defeats the point of
#: looking like ordinary HTTPS traffic.
VLESS_PORT = 443


def _b64url(raw: bytes) -> str:
    """Base64url without padding — the encoding ``xray x25519`` prints."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class RealityKeyPair:
    """An x25519 keypair in the base64url form Xray uses."""

    private_key: str
    public_key: str


@dataclass(frozen=True, slots=True)
class RealityCredentials:
    """The full set of secrets one Reality server needs."""

    uuid: str
    private_key: str
    public_key: str
    short_id: str
    sni: str = DEFAULT_SNI


def generate_x25519_keypair() -> RealityKeyPair:
    """Generate a Reality x25519 keypair.

    Equivalent to ``xray x25519``: raw 32-byte scalars, base64url-encoded
    without padding.
    """
    private = X25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return RealityKeyPair(
        private_key=_b64url(private_raw),
        public_key=_b64url(public_raw),
    )


def generate_client_uuid() -> str:
    """Generate the VLESS client id."""
    return str(uuid_module.uuid4())


def generate_short_id(length: int = 8) -> str:
    """Generate a Reality ``shortId``: lowercase hex, even length, max 16."""
    if length <= 0 or length > 16 or length % 2:
        raise ValueError("short id length must be an even number between 2 and 16")
    return secrets.token_hex(length // 2)


def generate_credentials(sni: str = DEFAULT_SNI) -> RealityCredentials:
    """Generate every secret for one server in a single call."""
    keys = generate_x25519_keypair()
    return RealityCredentials(
        uuid=generate_client_uuid(),
        private_key=keys.private_key,
        public_key=keys.public_key,
        short_id=generate_short_id(),
        sni=sni or DEFAULT_SNI,
    )


def build_xray_config(
    *,
    client_uuid: str,
    private_key: str,
    short_id: str,
    sni: str = DEFAULT_SNI,
    port: int = VLESS_PORT,
) -> dict:
    """Build the server-side ``/usr/local/etc/xray/config.json`` document.

    ``dest``/``serverNames`` point at the masquerade host: Reality proxies any
    handshake it cannot authenticate straight through to that real site, which
    is what makes an active probe see a genuine certificate chain.
    """
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "0.0.0.0",
                "port": port,
                "protocol": "vless",
                "tag": "vless-reality",
                "settings": {
                    "clients": [
                        {
                            "id": client_uuid,
                            "flow": "xtls-rprx-vision",
                        }
                    ],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": f"{sni}:443",
                        "xver": 0,
                        "serverNames": [sni],
                        "privateKey": private_key,
                        "shortIds": [short_id],
                    },
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                },
            }
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
        ],
        "routing": {
            "rules": [
                # Private ranges must never be reachable through the tunnel:
                # otherwise a customer can scan the provider's internal network.
                {
                    "type": "field",
                    "ip": ["geoip:private"],
                    "outboundTag": "block",
                },
                {
                    "type": "field",
                    "protocol": ["bittorrent"],
                    "outboundTag": "block",
                },
            ]
        },
    }


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(f"{pad}{line}" if line else "" for line in text.splitlines())


def build_cloud_init(
    *,
    client_uuid: str,
    private_key: str,
    short_id: str,
    sni: str = DEFAULT_SNI,
    port: int = VLESS_PORT,
    hostname: str | None = None,
) -> str:
    """Render a cloud-init user-data document for Ubuntu 22.04 / 24.04.

    Everything is written with ``write_files`` and then run once by
    ``runcmd``; that ordering matters because the installer must find the
    config already on disk, or it drops in its own empty template and the
    first ``systemctl start`` fails.

    BBR is enabled because the default CUBIC on a long RU↔EU path costs a
    noticeable fraction of the achievable throughput.
    """
    config = build_xray_config(
        client_uuid=client_uuid,
        private_key=private_key,
        short_id=short_id,
        sni=sni,
        port=port,
    )
    config_json = json.dumps(config, indent=2, ensure_ascii=False)

    hostname_block = f"hostname: {hostname}\n" if hostname else ""

    return f"""#cloud-config
{hostname_block}package_update: true
package_upgrade: false
packages:
  - curl
  - ca-certificates
  - unzip
  - jq

write_files:
  - path: /usr/local/etc/xray/config.json
    permissions: '0644'
    owner: root:root
    content: |
{_indent(config_json, 6)}

  - path: /etc/sysctl.d/99-xray.conf
    permissions: '0644'
    content: |
      net.core.default_qdisc=fq
      net.ipv4.tcp_congestion_control=bbr
      net.ipv4.ip_forward=1
      net.ipv4.tcp_fastopen=3

  - path: /usr/local/bin/install-xray.sh
    permissions: '0755'
    content: |
      #!/usr/bin/env bash
      set -euo pipefail

      export DEBIAN_FRONTEND=noninteractive

      # The official installer is idempotent and picks the latest release.
      curl -fsSL {XRAY_INSTALL_URL} -o /tmp/xray-install.sh
      bash /tmp/xray-install.sh install

      sysctl --system || true

      # Keep the firewall permissive for 443/22 only. ufw may be absent on
      # minimal cloud images, so every call is best-effort.
      if command -v ufw >/dev/null 2>&1; then
        ufw allow 22/tcp || true
        ufw allow {port}/tcp || true
        ufw --force enable || true
      fi

      systemctl enable xray
      systemctl restart xray

runcmd:
  - [ bash, -lc, "/usr/local/bin/install-xray.sh >> /var/log/xray-provision.log 2>&1" ]

final_message: "xray reality ready after $UPTIME seconds"
"""


def build_vless_uri(
    *,
    client_uuid: str,
    ip_address: str,
    public_key: str,
    short_id: str,
    sni: str = DEFAULT_SNI,
    name: str = "VPN",
    port: int = VLESS_PORT,
) -> str:
    """Render the ``vless://`` share link for a client app.

    Parameter order is fixed to match what the v2rayNG / Hiddify / Streisand
    importers are tested against; the fragment is percent-encoded so Cyrillic
    server names survive a round trip through a QR code.
    """
    query = (
        "security=reality"
        "&encryption=none"
        f"&pbk={public_key}"
        "&headerType=none"
        "&fp=chrome"
        "&type=tcp"
        "&flow=xtls-rprx-vision"
        f"&sni={sni}"
        f"&sid={short_id}"
    )
    fragment = quote(name, safe="")
    return f"vless://{client_uuid}@{ip_address}:{port}?{query}#{fragment}"


__all__ = [
    "DEFAULT_SNI",
    "SNI_POOL",
    "VLESS_PORT",
    "XRAY_INSTALL_URL",
    "RealityCredentials",
    "RealityKeyPair",
    "build_cloud_init",
    "build_vless_uri",
    "build_xray_config",
    "generate_client_uuid",
    "generate_credentials",
    "generate_short_id",
    "generate_x25519_keypair",
]
