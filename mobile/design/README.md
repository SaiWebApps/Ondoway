# Ondoway Mobile — Design System (source of truth)

**Current version: v10.** The canonical design system — all screens, light + dark — is the
published artifact **"Ondoway Mobile — Design System v10"**
(https://claude.ai/code/artifact/b9471f16-c7f3-46cc-a255-28f4abf0331c, updated 2026-08-05).

`design-system-v10.html` in this folder is a **committed snapshot** of that artifact, so the
repo is self-contained and the Flutter theme has a version-controlled reference. It is the raw
artifact export (includes the claude.ai frame runtime; open it in a browser to view the
screens). When the artifact iterates to v11+, re-snapshot here and bump this note.

## Core tokens (extracted from v10 — the input for `mobile/lib/theme/`)

Full light + dark. Encode these into a `Tokens` class + `ThemeData` (Slice 0.1).

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
NOT per-lens-category colors at the token level. Confirm LensTile category treatment against
the v10 lens screens when building that component.
