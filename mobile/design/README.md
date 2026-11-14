# Ondoway Mobile — Design System (source of truth)

**Current version: v10.** `design-system-v10.html` in this folder is the design system — all
screens, light + dark — committed so the repo is self-contained and the Flutter theme has a
version-controlled reference. It is an export (it includes its own frame runtime; open it in
a browser to view the screens). A new version replaces this file and this note together.

## Core tokens (from `design-system-v10.html` — the input for `mobile/lib/theme/`)

Full light + dark. These are encoded in `OndowayColors` (`mobile/lib/theme/tokens.dart`) and
`buildOndowayTheme` (`mobile/lib/theme/theme.dart`).

| Token | Light | Dark |
|---|---|---|
| Accent (cobalt) | `#2c6cc0` | `#7bb2f5` |
| Accent deep | `#1e4f92` | `#4c86d6` |
| Accent light | `#7bb2f5` | — |
| Background | `#e9e5db` | `#101218` |
| Card | `#ffffff` | `#20242c` |
| Page panel | `#f6f4f0` | `#12151b` |
| Ink (text) | `#20242c` | `#f6f4f0` |
| Ink soft | `#3a3f49` | `#c7cbd3` |
| Ink mute | `#5b6069` | `#8b909b` |
| Line | `#ded8cb` | `#2e333d` |
| Line soft | `#e8e3d8` | `#252a33` |
| Spark (warm highlight) | `#e8934a` | `#e8934a` |
| Elevation | `--lift`, `--lift-lg` (see snapshot for exact rgba) | ✓ |

**Type:** Fraunces (display, `ui-serif`/Georgia fallback) · Space Grotesk (body,
`system-ui` fallback) · Space Mono (mono, Menlo fallback).

**Color model:** a single cobalt accent + warm-neutral system + one warm "spark" highlight —
NOT per-lens-category colors at the token level. The lens category tile colors sit beneath
the accent, in `mobile/lib/theme/lens_palette.dart`.
