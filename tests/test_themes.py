"""Theme catalogue and the theme-preference endpoint.

The three sources of truth for a theme — the `Theme` enum, the Python
catalogue, and the CSS/JS registries in the frontend — are asserted to match
here. A theme present in only some of them silently renders as the default,
which is a bug with no error message anywhere.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.themes import THEMES
from app.main import create_app
from app.models import Base
from app.models.enums import DEFAULT_THEME, Theme
from app.models.user import User

FRONTEND = Path(__file__).parent.parent / "frontend" / "src"

TELEGRAM_ID = 515000515


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Real app against a throwaway SQLite file, with one seeded user.

    Mirrors the fixture in `test_api_routes.py` rather than importing it: the
    theme endpoints need no Dynadot client, and sharing the heavier fixture
    would couple these tests to the registrar fake.
    """
    db_path = tmp_path / "themes.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import app.core.config as config_module
    import app.db.session as session_module

    config_module.get_settings.cache_clear()
    new_settings = config_module.Settings()
    monkeypatch.setattr(config_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)

    async def setup() -> None:
        async with session_module.get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = session_module.get_session_factory()
        async with factory() as session:
            session.add(User(telegram_id=TELEGRAM_ID, username="themer"))
            await session.commit()

    asyncio.run(setup())

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def seeded_user() -> int:
    return TELEGRAM_ID


EXPECTED_IDS = [
    "onyx-neon",
    "linear-indigo",
    "sunset-crimson",
    "nordic-mist",
    "emerald-spring",
    "solar-amber",
]

CSS_VARIABLES = (
    "--c-bg",
    "--c-surface",
    "--c-elevated",
    "--c-line",
    "--c-ink",
    "--c-muted",
    "--c-faint",
    "--c-accent",
    "--c-accent-ink",
    "--c-ok",
    "--c-warn",
    "--c-danger",
)


def test_enum_ships_the_five_commercial_themes() -> None:
    assert [theme.value for theme in Theme] == EXPECTED_IDS


def test_default_theme_is_a_member() -> None:
    assert DEFAULT_THEME in set(Theme)


def test_catalogue_covers_every_enum_member_in_order() -> None:
    assert [info.id for info in THEMES] == list(Theme)


def test_catalogue_entries_are_complete() -> None:
    for info in THEMES:
        assert info.name
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", info.accent), info.accent
        assert info.scheme in {"light", "dark"}


def test_at_least_one_light_and_one_dark_theme() -> None:
    schemes = {info.scheme for info in THEMES}
    assert schemes == {"light", "dark"}


def test_css_defines_a_complete_block_per_theme() -> None:
    css = (FRONTEND / "index.css").read_text(encoding="utf-8")
    for theme in Theme:
        pattern = r"\[data-theme='" + re.escape(theme.value) + r"'\]\s*\{([^}]*)\}"
        match = re.search(pattern, css)
        assert match, f"no CSS block for {theme.value}"
        body = match.group(1)
        for variable in CSS_VARIABLES:
            assert variable in body, f"{theme.value} is missing {variable}"


def test_frontend_registry_matches_the_enum() -> None:
    source = (FRONTEND / "lib" / "themes.js").read_text(encoding="utf-8")
    ids = re.findall(r"id:\s*'([a-z-]+)'", source)
    assert ids == [theme.value for theme in Theme]


def test_frontend_accents_match_the_python_catalogue() -> None:
    """The picker paints a swatch before the theme is applied, so the accent is
    duplicated in JS — the duplicate must not drift."""
    source = (FRONTEND / "lib" / "themes.js").read_text(encoding="utf-8")
    pairs = dict(
        re.findall(r"id:\s*'([a-z-]+)',[\s\S]*?accent:\s*'(#[0-9a-fA-F]{6})'", source)
    )
    for info in THEMES:
        assert pairs[info.id.value].lower() == info.accent.lower(), info.id


def _channels(body: str, variable: str) -> tuple[int, int, int]:
    match = re.search(rf"{re.escape(variable)}:\s*(\d+)\s+(\d+)\s+(\d+)", body)
    assert match, f"{variable} not found"
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        ratio = value / 255
        return ratio / 12.92 if ratio <= 0.03928 else ((ratio + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    lighter = max(_relative_luminance(first), _relative_luminance(second))
    darker = min(_relative_luminance(first), _relative_luminance(second))
    return (lighter + 0.05) / (darker + 0.05)


def test_every_theme_meets_wcag_aa_contrast() -> None:
    """Body text, secondary text and primary-button text must all pass AA.

    A commercial theme that ships unreadable buttons is worse than no theme.
    `--c-accent-ink` on `--c-accent` is the pair that actually breaks: a bright
    accent needs dark ink, and it is easy to copy `255 255 255` into a new
    theme without checking.
    """
    css = (FRONTEND / "index.css").read_text(encoding="utf-8")
    for theme in Theme:
        pattern = r"\[data-theme='" + re.escape(theme.value) + r"'\]\s*\{([^}]*)\}"
        match = re.search(pattern, css)
        assert match
        body = match.group(1)

        background = _channels(body, "--c-bg")
        checks = {
            "ink on bg": (_channels(body, "--c-ink"), background, 4.5),
            # Secondary text is still body copy, so it gets the full 4.5 too.
            "muted on bg": (_channels(body, "--c-muted"), background, 4.5),
            "accent-ink on accent": (
                _channels(body, "--c-accent-ink"),
                _channels(body, "--c-accent"),
                4.5,
            ),
        }
        for label, (foreground, against, minimum) in checks.items():
            ratio = _contrast(foreground, against)
            assert ratio >= minimum, (
                f"{theme.value}: {label} is {ratio:.2f}:1, needs {minimum}:1"
            )


def test_themes_endpoint_lists_every_theme(client) -> None:
    response = client.get("/api/v1/themes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["default"] == DEFAULT_THEME.value
    assert [item["id"] for item in payload["themes"]] == EXPECTED_IDS


def test_new_user_gets_the_default_theme(client, seeded_user) -> None:
    response = client.get(f"/api/v1/users/{seeded_user}")
    assert response.status_code == 200
    assert response.json()["theme"] == DEFAULT_THEME.value


def test_set_theme_persists_and_is_returned(client, seeded_user) -> None:
    patch = client.patch(
        f"/api/v1/users/{seeded_user}/theme", json={"theme": "sunset-crimson"}
    )
    assert patch.status_code == 200
    assert patch.json()["theme"] == "sunset-crimson"

    # Re-read: the value must come back from the database, not just the echo.
    assert client.get(f"/api/v1/users/{seeded_user}").json()["theme"] == "sunset-crimson"


def test_set_theme_rejects_an_unknown_theme(client, seeded_user) -> None:
    response = client.patch(
        f"/api/v1/users/{seeded_user}/theme", json={"theme": "hot-pink"}
    )
    assert response.status_code == 422


def test_set_theme_on_a_missing_user_is_404(client) -> None:
    response = client.patch("/api/v1/users/999000999/theme", json={"theme": "onyx-neon"})
    assert response.status_code == 404


def test_theme_does_not_disturb_the_language_preference(client, seeded_user) -> None:
    client.patch(f"/api/v1/users/{seeded_user}/language", json={"language": "en"})
    client.patch(f"/api/v1/users/{seeded_user}/theme", json={"theme": "emerald-spring"})
    user = client.get(f"/api/v1/users/{seeded_user}").json()
    assert user["language"] == "en"
    assert user["theme"] == "emerald-spring"


def test_migration_enum_matches_the_model() -> None:
    """The Alembic enum is written by hand; a drift here only fails in prod."""
    migration = (
        Path(__file__).parent.parent
        / "migrations"
        / "versions"
        / "7c3b91d2f4a8_add_user_theme.py"
    ).read_text(encoding="utf-8")
    for theme in Theme:
        assert f"'{theme.name}'" in migration, theme.name
