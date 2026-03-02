# Golden Dataset Verification Summary

**Date**: 2026-03-02
**Verified by**: Independent Claude agent (agent ID: a259a25b24522937b)
**Dataset**: `facility_interpreter_golden.py` — 24 test cases
**Verdict**: All 24 cases correct. No errors found.

---

## What the agent did

1. Read the golden dataset (`facility_interpreter_golden.py`)
2. Ran `verify_golden_dataset.py` to get MBTA API route direction data
3. Made additional live MBTA API calls to verify ambiguous cases (facility properties, stop routing chains)
4. Independently assessed each case's `expected_lines` and `expected_direction` against MBTA network topology

---

## Key findings by pattern

### Pattern 1 — Explicit line name (Cases 01–05) ✓
All correct. Cases 03, 04, 05 had false positives in the verification script (station names matching API terminus keywords), but the labels are right.

- **Case 05 note**: Elevator 909 at North Station is physically in the accessibility chain for CR passengers too, but `expected_lines=["Green-D","Green-E","Orange"]` is correct — the facility name is the authoritative source for what the elevator *itself* serves.

### Pattern 2 — Terminus references (Cases 06–13) ✓
All correct. The 8 "direction mismatch" flags from `verify_golden_dataset.py` were all string format issues ("north" vs "northbound") — not actual errors.

- **Case 12 note (Ashmont 968)**: Agent confirmed the "Mattapan" false match was entirely spurious. "Alewife platform" at Ashmont is unambiguously Red Line only — Mattapan has no connection to Alewife. Other elevators at Ashmont (969, 970) confirm the naming convention.

### Pattern 3 — Green Line directional labels (Cases 14–18) ✓
All correct. The 4 "NO MATCH" results from the verification script are expected — MBTA wayfinding labels ("Park Street & North", "Copley & West") don't appear as terminus names in the routes API.

- **Case 18 note (Copley 977)**: "Kenmore & West, Heath Street" is MBTA's compound label for one shared westbound platform serving all four outbound Green branches. `expected_lines=["Green-B","Green-C","Green-D","Green-E"]` is correct.

### Pattern 4 — Cross-platform connectors (Cases 19–21) ✓
All correct. Direction format mismatches in verification script only.

- **Case 19**: Elevator 998 at Downtown Crossing physically bridges the Orange (Oak Grove) and Red (Alewife) platforms — serves both lines, `direction=None` is correct.
- **Cases 20/21**: Confirmed by the existence of parallel elevator 870 ("Ashmont/Braintree platform") which would be `direction=southbound` — validates the directional convention used for cases 20/21.

### Pattern 5 — Generic/shared (Cases 22–24) ✓
All correct. False positives in verification script from station name keyword collisions.

---

## Why the verification script showed 15 "issues"

All 15 were false positives. They break down as:

| Type | Count | Cause |
|---|---|---|
| Direction format mismatch | 8 | Script compares "north" (API) vs "northbound" (dataset) |
| Station name keyword collision | 5 | "Braintree", "North Station", "Forest Hills", "Ashmont" appear in facility names as station prefixes, not direction cues |
| Partial compound label match | 1 | Case 18: script matched only "Heath Street" → Green-E, missing "Kenmore & West" → Green-B/C/D |
| Pattern 3 no-match | 4 | Expected — directional labels not in routes API |

---

## Conclusion

The golden dataset is reliable and ready to use for benchmarking. The `verify_golden_dataset.py` script has a structural limitation — it can verify terminus-based cases but cannot validate Green Line directional label cases (Pattern 3), and it conflates station name prefixes with direction keywords. Its output should be interpreted with these limitations in mind.
