# Design themes

Five commercial themes, switchable at runtime in both the TMA and the admin
panel. The choice is stored on the user profile (so it follows the account)
and mirrored into `localStorage` (so the first paint is already correct).

| id | name | scheme | character |
| --- | --- | --- | --- |
| `onyx-neon` | Onyx Neon | dark | Near-black surfaces, electric blue accent. The default. |
| `vercel-mono` | Vercel Mono | dark | Pure greyscale, white accent, maximum contrast. |
| `linear-indigo` | Linear Indigo | dark | Cool blue-tinted greys, violet accent. |
| `solana-cyber` | Solana Cyber | dark | Deep purple-black, mint accent. |
| `nordic-mist` | Nordic Mist | light | The one light theme; also the light-client fallback. |

## How it works

Every theme is a complete set of the same twelve CSS variables, declared in a
`:root[data-theme='<id>']` block in `frontend/src/index.css`:

```
--c-bg  --c-surface  --c-elevated  --c-line
--c-ink --c-muted    --c-faint
--c-accent --c-accent-ink
--c-ok  --c-warn     --c-danger
```

Tailwind maps those to colour aliases (`bg-surface`, `text-ink`,
`border-line`, …) in `tailwind.config.js`. Components only ever use the
aliases, so **switching a theme is a single attribute write** —
`document.documentElement.dataset.theme = id` — with no re-render and no
per-theme component code.

Values are raw RGB channels (`8 9 10`, not `#08090a`) so Tailwind's
`/<alpha-value>` modifier keeps working: `bg-accent/90`, `ring-accent/60`.

## Resolution order

`useTheme` (`frontend/src/hooks/useTheme.js`) resolves three sources, in
priority order — each fixes a different failure:

1. **`localStorage`** — read synchronously in `main.jsx`/`admin.jsx` *before*
   React mounts. Without it, every launch flashes the default palette for as
   long as the profile request takes.
2. **The user profile** (`GET /users/:id` → `theme`) — wins once it arrives,
   so the choice follows the account to a new device. A late response is
   ignored if the user has already picked in this session.
3. **Telegram's `colorScheme`** — the fallback for a user who has never
   chosen: a light client gets `nordic-mist` rather than a black app.

Switching is **optimistic**: `selectTheme` paints and stores immediately, then
`PATCH /users/:id/theme` in the background. A failed PATCH is not reverted —
the user can see the new theme, and `localStorage` keeps it across reloads.

The admin panel passes `persist: false` (it has no `telegram_id`), so the
operator console's theme is a per-browser preference.

## Accessibility

`tests/test_themes.py::test_every_theme_meets_wcag_aa_contrast` enforces 4.5:1
on `--c-ink`/`--c-muted` over `--c-bg` and on `--c-accent-ink` over
`--c-accent`.

That last pair is the one that actually breaks. A bright accent needs **dark**
ink: white on `onyx-neon`'s blue is only 3.3:1. Four of the five themes
therefore use near-black `--c-accent-ink`, and only `nordic-mist` uses white.
Do not copy `255 255 255` into a new theme without checking.

## Adding a theme

Four edits, all asserted to stay in sync by the test suite — a theme present
in only some of them renders as the default with no error anywhere:

1. `app/models/enums.py` — a `Theme` member.
2. `app/core/themes.py` — a `ThemeInfo` entry (name, accent, scheme).
3. `frontend/src/lib/themes.js` — a `THEMES` entry (accent duplicated here
   because the picker paints a swatch *before* the theme is applied).
4. `frontend/src/index.css` — a `:root[data-theme='<id>']` block with all
   twelve variables.

Then an Alembic migration, since the column is a string enum.

## API

```
GET   /api/v1/themes              → { default, themes: [{ id, name, accent, scheme }] }
PATCH /api/v1/users/{tg_id}/theme   body { "theme": "solana-cyber" } → UserRead
```

Unknown ids are rejected with 422; a missing user is 404.
