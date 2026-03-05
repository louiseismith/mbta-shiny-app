# Facility Line Mapping — Seed Notes

## What this is

`facility_line_mapping.json` classifies all 526 MBTA accessibility facilities
(elevators, escalators, ramps, portable lifts) by which transit line(s) and
platform direction each serves. It is the seed dataset for the
`facility_line_mapping` Supabase table, which enables per-line trip verdicts
in the app.

## Files

| File | Description |
|---|---|
| `seed_facilities_input.json` | Raw facility data from MBTA API + station routes from Supabase |
| `seed_facility_line_mapping.py` | Classification script (run to regenerate or upload) |
| `facility_line_mapping.json` | Output: 526 classified facilities, ready to upload |
| `verify_line_assignments.py` | Verification script — checks mapping for correctness |

## How to upload to Supabase

```bash
python seed_facility_line_mapping.py --upload
```

Creates the `facility_line_mapping` table if it doesn't exist. Uses
`ON CONFLICT DO UPDATE ... WHERE source != 'manual'` — rows marked
`source='manual'` are never overwritten.

## Classification approach

Rather than calling an LLM at runtime, MBTA topology knowledge is encoded
directly in the script as data structures and rule-based logic. This is
reproducible, auditable, and zero-cost.

### Supabase table schema

```sql
CREATE TABLE facility_line_mapping (
    facility_id   TEXT PRIMARY KEY,
    facility_name TEXT NOT NULL,
    station_name  TEXT NOT NULL,
    lines         TEXT[],     -- null if unresolved (P0)
    direction     TEXT,       -- null if not direction-specific
    pattern       SMALLINT,
    source        TEXT NOT NULL,  -- 'claude', 'ollama:gemma3:27b', 'unresolved', 'manual'
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
```

### Naming patterns

| Pattern | Description | Example |
|---|---|---|
| P0 | No routes mapped (ferry, Silver Line, Lynn) | Courthouse Escalator |
| P1 | Explicit line name in facility description | "Orange Line platform to lobby" |
| P2 | Terminus reference | "Alewife platform" → Red northbound |
| P3 | Green Line directional label | "Copley & West" → westbound |
| P4 | Cross-platform connector (two lines) | "Oak Grove platform to Alewife platform" |
| P5 | Generic / shared infrastructure | "Lobby to street", ramp access |

### Classification results

| Pattern | Count |
|---|---|
| P0 (no routes) | 35 |
| P1 (explicit line) | 58 |
| P2 (terminus ref) | 119 |
| P3 (Green directional) | 49 |
| P4 (cross-platform) | 18 |
| P5 (generic/shared) | 247 |
| **Total** | **526** |

About one-third of facilities have a direction assigned; the rest are non-directional
(shared infrastructure, cross-platform connectors, or single-line stations where
direction is implied by context).

### Classification priority order

For multi-route stations, checks run in this order (first match wins):

1. Explicit line keyword (P1) — e.g. "Orange Line", "Commuter Rail", "Green Line"
2. Green Line directional label (P3) — "Park Street & North", "Copley & West", etc.
3. Cross-platform connector (P4) — name references two distinct lines/termini
4. Terminus reference (P2) — "Alewife", "Oak Grove", "Riverside", etc.
5. Combined platform (P4/P3) — e.g. "Forest Hills, Copley & West platform"
6. Generic/shared fallback (P5)

### MBTA topology encoded in script

**`TERMINUS_MAP`** — terminus name → (line, direction):
- Red: Alewife (NB), Ashmont (SB), Braintree (SB), Ashmont/Braintree (SB)
- Orange: Oak Grove (NB), Forest Hills (SB)
- Blue: Bowdoin (WB), Wonderland (EB)
- Green branches: Boston College/Green-B (WB), Cleveland Circle/Green-C (WB),
  Riverside/Green-D (WB), Lechmere/Green-E (EB — inbound convention)
- Mattapan: Mattapan (SB)

**Green Line direction convention**: inbound = eastbound, outbound = westbound,
consistently across all Green Line stations. "Park Street & North" /
"North Station & North" / "Lechmere & North" = eastbound.
"Copley & West" / "Kenmore & West" / "Heath Street" / "Westbound" = westbound.

### Manual overrides

~60 facilities have hardcoded results in `MANUAL_OVERRIDES` for cases the
rule-based logic can't handle cleanly:

- **South Station**: Red platform vs CR platform vs Silver Line (not in
  `station_routes`) vs shared lobby facilities
- **Ashmont**: Mattapan trolley transfer point — some facilities serve both
  Red southbound and Mattapan
- **North Station**: Combined "Forest Hills, Copley & West" platform serving
  Orange southbound + Green westbound together
- **State**: Multi-level connectors linking Orange NB ↔ Orange SB ↔ Blue EB
- **Park Street**: Red center platform vs Green eastbound vs Green westbound
- **Government Center**: Combined "Copley & West, Blue Line" exit

### P0 facilities (no lines — out of scope)

35 facilities have no rapid transit routes mapped:
- **Ferry terminals** (Boat-*): MBTA ferry, not in `station_routes`
- **Silver Line stations** (Courthouse, World Trade Center): type 3 bus, not in `station_routes`
- **Lynn**: Commuter Rail station not yet in `station_routes` cache

These will remain `lines=null` in Supabase and be surfaced as-is in the UI.

## Bugs found and fixed during review

### Bug A — Missing Green Line branch termini

"Riverside", "Boston College", and "Cleveland Circle" were absent from
`TERMINUS_MAP`, so mobile lifts labelled with these termini were classified
as P5 (generic, no direction) instead of P2 (westbound). 10 facilities fixed:

| Facility | Name |
|---|---|
| portlift-fenwy | Fenway mobile lift (Riverside) |
| portlift-newto | Newton Centre mobile lift (Riverside) |
| portlift-newtn-1 | Newton Highlands mobile lift (Riverside) |
| portlift-cool-0 | Coolidge Corner mobile lift (Cleveland Circle) |
| portlift-bcnwa-0 | Washington Square mobile lift (Cleveland Circle) |
| portlift-bucen | Boston University Central mobile lift (Boston College) |
| portlift-babck-0 | Babcock Street mobile lift (Boston College) |
| portlift-harvd-1 | Harvard Avenue mobile lift (Boston College) |
| portlift-amory-0 | Amory Street mobile lift (Boston College) |
| 921 | Prudential Elevator 921 (Lechmere platform to lobby) |

**Fix**: Added Riverside, Boston College, Cleveland Circle, and Lechmere to
`TERMINUS_MAP` with correct lines and directions.

### Bug B — `_is_generic` swallowing P1 facilities

When a facility name contained an explicit line keyword (e.g. "Commuter Rail")
but also mentioned "lobby" without "platform", `_is_generic` fired before the
P1 check. These were classified P5 with all station routes instead of P1 with
only the named line's routes. 11 facilities fixed:

| Facility | Name |
|---|---|
| 854, 855, 856 | Back Bay Elevators (Commuter Rail track X to lobby) |
| 142, 143, 144 | Back Bay Escalators (Commuter Rail tracks X to lobby) |
| 849 | Ruggles Elevator 849 (Commuter Rail tracks 1 and 3 to lobby) |
| 909, 912 | North Station Elevators (Orange Line, Green Line lobby to X) |
| 385 | North Station Escalator 385 (Valenti Way to Orange Line, Green Line lobby) |
| 732 | North Station Elevator 732 (Passageway to Commuter Rail, TD Garden) |

**Fix**: Moved P1 check before `_is_generic` check in `classify_facility()`.

### Bug C — P4 before P3 (fixed earlier)

Facilities at single-direction Green Line stations (Arlington, Copley, Kenmore)
with labels like "Copley & West platform to lobby" were misclassified as P4
(cross-platform connector) instead of P3 (Green Line directional) because the
P4 check ran first and matched the Green Label as a multi-route reference.

**Fix**: Reordered checks so P3 runs before P4.

### Bug D — Ashmont elevator 969 missing Mattapan line

Elevator 969 ("Alewife platform to Mattapan Line lobby") connects the Red
northbound platform to the Mattapan transfer lobby. The terminus check
("Alewife") fired first and classified it as P2 with `lines=["Red"]` only,
missing the Mattapan line referenced in the name.

**Fix**: Reclassified as P4 (cross-platform connector) with
`lines=["Mattapan", "Red"]`, `direction=null`.

Found by `verify_line_assignments.py` name-consistency check.

## Validation

### Golden benchmark cases
All 24 golden benchmark cases produce correct `lines` and `direction` values.

### Automated verification (`verify_line_assignments.py`)

491 facilities checked (35 P0 skipped), 0 errors, 0 warnings.

Five checks run across all facilities:

| Check | What it catches |
|---|---|
| **Route validity** (`--routes`) | Assigned line doesn't serve the station (checked against Supabase `station_routes`) |
| **Name consistency** | Line keyword in facility name not reflected in assigned `lines` (e.g., name says "Mattapan" but `lines` has only `["Red"]`) |
| **Pattern rules** | Structural invariants per pattern: P1 has lines, P2/P3 have direction, P3 is Green-only, P5 matches all station routes with no direction, P4 has lines |
| **Direction sanity** | Impossible line/direction combos (e.g., Red + eastbound, Blue + northbound) |
| **Terminus direction** | P2 terminus name implies wrong direction (e.g., "Alewife" but direction is southbound) |

```bash
# Offline checks (fast, no network):
python verify_line_assignments.py

# Full checks including route validity (reads from Supabase):
python verify_line_assignments.py --routes
```

### Data uploaded to Supabase
Seed uploaded via `python seed_facility_line_mapping.py --upload`.
