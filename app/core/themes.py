"""Catalogue of the commercial design themes.

The actual colours live in `frontend/src/index.css` as `[data-theme=...]`
blocks — CSS is the only place that can repaint without a round-trip. This
module keeps the *metadata* the UI needs to render a picker (display name, the
accent swatch, whether the palette is light or dark) so that the TMA and the
admin panel do not each hard-code their own list, and so `/themes` can serve
it to any future client.

Adding a theme means: one entry here, one `[data-theme='<id>']` block in
`index.css`, and one member in `models.enums.Theme`. The test suite asserts
those three stay in sync, because a theme that exists in only two of them
renders as the default with no error anywhere.
"""

from __future__ import annotations

from app.models.enums import DEFAULT_THEME, Theme
from app.schemas import ThemeInfo, ThemesResponse

# Accent values are duplicated from the CSS deliberately: the picker has to
# paint a swatch before the theme is applied, so it cannot read the variable
# from the document.
THEMES: tuple[ThemeInfo, ...] = (
    ThemeInfo(
        id=Theme.ONYX_NEON,
        name="Cyber Neon",
        accent="#00f0ff",
        scheme="dark",
    ),
    ThemeInfo(
        id=Theme.LINEAR_INDIGO,
        name="Linear Indigo",
        accent="#8b7fff",
        scheme="dark",
    ),
    ThemeInfo(
        id=Theme.SUNSET_CRIMSON,
        name="Sunset Crimson",
        accent="#ff3366",
        scheme="dark",
    ),
    ThemeInfo(
        id=Theme.NORDIC_MIST,
        name="Nordic Mist",
        accent="#2f6fed",
        scheme="light",
    ),
    ThemeInfo(
        id=Theme.EMERALD_SPRING,
        name="Emerald Spring",
        accent="#047857",
        scheme="light",
    ),
    ThemeInfo(
        id=Theme.SOLAR_AMBER,
        name="Solar Amber",
        accent="#c2410c",
        scheme="light",
    ),
)


def themes_response() -> ThemesResponse:
    return ThemesResponse(default=DEFAULT_THEME, themes=list(THEMES))
