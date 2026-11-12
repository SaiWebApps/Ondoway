# Graph Editor — User Guide

Interactive canvas-based editor for viewing and modifying the Ondoway knowledge graph.

## Getting Started

```bash
# 1. Bootstrap (first time only)
make setup              # Installs deps, starts the databases, provisions data

# 2. Start the API server
make api                # FastAPI at http://localhost:8000

# 3. Open the editor
open http://localhost:8000/editor
```

The editor loads the full graph from `/api/v1/graph` on startup. The connection badge in the header turns **green** ("connected") when the API is reachable. If it turns **red**, check that the API is running and Neo4j is up (`make db-status`).

The editor needs the graph routes (`/api/v1/graph`, `/api/v1/nodes`, `/api/v1/edges`,
`/api/v1/schema`). The API mounts them only when `WORKBENCH_API_ENABLED` is true. The
local, test and workbench profiles under `config/profiles/` set it to true. The public
deployment sets it to false, so the editor page loads there but cannot reach the graph.

## Interface Overview

```
┌──────────────────────────────────────────────────────┐
│  Ondoway Graph Editor     [connected] 12 nodes 15 edges │
├──────────────────────────────────────────────────────┤
│  [+ Node] [+ Edge] [Refresh]                         │
│                                                      │
│           ◉ User ─── ◉ Profile ── ◉ Family    ┌─────┤
│                       │                       │ Side │
│                      ◉ Trip ── ◉ ItineraryItem│Panel │
│                                  │            │      │
│           ◉ Lens ── ◉ Beat ── ◉ POI ── ◉ Area │Forms │
│                                               └─────┤
│  [User] [Profile] [Trip] [POI] [Beat] [Lens] [Item]  │
└──────────────────────────────────────────────────────┘
   Header          Canvas                      Panel    Legend
```

### Header
- **Connection badge**: green = connected, red = failed
- **Stats**: live count of nodes and edges (updates after every operation)

### Floating Toolbar (top-left)
- **+ Node**: open the create-node form
- **+ Edge**: open the create-edge form
- **Refresh**: reload the full graph from the API

### Canvas
- Force-directed graph visualization on an HTML5 canvas
- Nodes are color-coded circles; edges are arrows with labels
- Physics simulation runs for ~5 seconds, then settles

### Legend (bottom)
- Color key for 7 of the 9 node types. Area and Family have no entry in the legend.

### Side Panel (right)
- Slides in when creating or editing a node/edge
- Contains dynamic forms generated from the API schema
- Close with the **X** button or press **Escape**

## Node Types & Colors

The graph has 9 node types. The editor gives 7 of them their own color. Area and Family
are drawn grey, at 14 px, and do not appear in the legend.

| Type           | Color      | Size  |
|----------------|------------|-------|
| User           | Blue       | 18 px |
| Profile        | Light blue | 16 px |
| Trip           | Sky blue   | 16 px |
| POI            | Green      | 20 px |
| NarrativeBeat  | Light green| 14 px |
| Lens           | Lime green | 14 px |
| ItineraryItem  | Amber      | 12 px |
| Area           | Grey       | 14 px |
| Family         | Grey       | 14 px |

## Relationship Types

14 edge types connect nodes in the graph:

| Type            | From → To                       |
|-----------------|---------------------------------|
| HAS_PROFILE     | User → Profile                  |
| IS_CAPTAIN_OF   | Profile → Trip                  |
| IS_CREW_OF      | Profile → Trip                  |
| MEMBER_OF       | Profile → Family                |
| DAY_OF          | Trip → Family                   |
| PREFERS_LENS    | Profile → Lens                  |
| HAS_STOP        | Trip → ItineraryItem            |
| ASSIGNED_TO     | ItineraryItem → Profile         |
| AT_POI          | ItineraryItem → POI             |
| PLAYS_BEAT      | ItineraryItem → NarrativeBeat   |
| HAS_BEAT        | POI or Area → NarrativeBeat     |
| TAGGED_WITH     | NarrativeBeat → Lens            |
| IS_PARENT_OF    | Lens → Lens                     |
| WITHIN          | POI or Area → Area              |

## Mouse & Keyboard Controls

### Mouse
| Action                | Effect                              |
|-----------------------|-------------------------------------|
| Hover over node       | Tooltip shows type, ID, properties  |
| Click node            | Select — opens edit panel           |
| Drag node             | Reposition on canvas                |
| Click edge            | Select — opens edit panel           |
| Click empty space     | Deselect / close panel              |

### Keyboard
| Key              | Effect                                   |
|------------------|------------------------------------------|
| Escape           | Close the side panel                     |
| Delete/Backspace | Delete selected node or edge (when not typing in a form field) |

## Creating a Node

1. Click **+ Node** in the toolbar
2. Select a node type from the dropdown (e.g., "POI")
3. The form populates with fields from the schema — required fields are marked with a red **\***
4. Fill in the properties and click **Save**
5. The new node appears on the canvas

### Example: Creating a POI

| Field                | Required | Default |
|----------------------|----------|---------|
| name                 | Yes      | —       |
| city_name            | Yes      | —       |
| short_description    | No       | ""      |
| latitude             | Yes      | —       |
| longitude            | Yes      | —       |
| importance_tier      | Yes (1–5)| —       |
| trigger_radius       | No       | 10      |
| typical_duration_min | No       | 30      |
| kid_friendly         | No       | "yes"   |

## Creating an Edge

1. Click **+ Edge** in the toolbar
2. Select a relationship type from the dropdown (e.g., "ASSIGNED_TO")
3. Click the **Source** button — it turns orange; cursor changes to crosshair
4. Click a node on the canvas to pick it as the source — button turns green
5. The **Target** button activates automatically — click a node for the target
6. Fill in any edge properties (if the relationship type has them)
7. Click **Save**

## Editing

1. Click any node or edge on the canvas
2. The side panel opens with the entity's current properties
3. Read-only fields (ID, label/type, created date) appear at the top
4. Modify editable properties in the form
5. Click **Save** to apply changes

## Deleting

1. Click a node or edge to select it
2. Either:
   - Click the **Delete** button in the side panel, or
   - Press **Delete** / **Backspace** (while not typing in a form field)
3. Confirm in the dialog

Deleting a node also deletes all its connected edges.

## Working with Local Data

`make setup` fills the local development graph from two sources:

- **Seed data** (`src/seed/`): 8 parent lenses and 21 child lenses linked by
  `IS_PARENT_OF`, one test user with two profiles in one family, three Paris
  POIs with their beats, and one sample trip with its stops.
- **The committed city corpus**: every city under `data/` that has a
  `poi-raw.json`, loaded the same way a deploy loads it.

Edits in the editor change the local dev graph only. The committed files under
`data/` stay the same. Every target that needs dev data, such as `make api`, `make test`
and `make tour-build`, first checks that the dev graph matches those files
(`make db-parity` runs the same check). On a mismatch it reloads those files into the
graph, but it never deletes. So a POI, beat or area that you add makes the check fail
and the target refuse to run. Seed nodes outside the city corpus, such as the test
user and its trip, are not checked.

To get back to a clean graph, delete the local dev database and provision it again:

```bash
make db-reset DB=dev    # Delete the local dev graph (never cloud)
make setup              # Start it again and reload the seed data and city corpus
```

## Troubleshooting

| Symptom                        | Fix                                   |
|--------------------------------|---------------------------------------|
| Badge shows "connection failed"| Run `make api` to start the server    |
| No nodes on canvas             | Run `make setup` to provision data    |
| Nodes fly off screen           | Click Refresh — physics will re-settle|
| Form fields missing            | Schema endpoint may be down — check `curl http://localhost:8000/api/v1/schema/nodes` |
