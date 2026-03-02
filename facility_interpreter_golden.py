# facility_interpreter_golden.py
# Golden dataset for benchmarking the facility name interpreter agent.
# Each case has a real MBTA facility name, its station, the routes serving
# that station, and the expected interpretation.
#
# expected_lines: subset of station routes that this facility specifically serves
# expected_direction: "northbound", "southbound", "eastbound", "westbound", or None
#                     None = facility serves all lines/directions at the station
# pattern: 1=explicit line name, 2=terminus reference, 3=Green Line directional,
#          4=cross-platform connector, 5=generic/shared

GOLDEN = [

    # -------------------------------------------------------------------------
    # PATTERN 1 — Explicit line name in facility description
    # Model should get these right easily — mostly a sanity check.
    # -------------------------------------------------------------------------

    {
        "facility_name": "Back Bay Elevator 853 (Orange Line platform to lobby)",
        "station_name": "Back Bay",
        "routes": ["CR-Franklin", "CR-Needham", "CR-Providence", "CR-Worcester", "Orange"],
        "expected_lines": ["Orange"],
        "expected_direction": None,
        "notes": "Explicitly says Orange Line. Direction not determinable from name alone.",
        "pattern": 1,
    },
    {
        "facility_name": "Back Bay Elevator 854 (Commuter Rail track 2 to lobby)",
        "station_name": "Back Bay",
        "routes": ["CR-Franklin", "CR-Needham", "CR-Providence", "CR-Worcester", "Orange"],
        "expected_lines": ["CR-Franklin", "CR-Needham", "CR-Providence", "CR-Worcester"],
        "expected_direction": None,
        "notes": "Explicitly says Commuter Rail. All CR lines at this station share the track.",
        "pattern": 1,
    },
    {
        "facility_name": "Braintree Elevator 811 (Red Line platform to lobby)",
        "station_name": "Braintree",
        "routes": ["CR-Kingston", "CR-NewBedford", "Red"],
        "expected_lines": ["Red"],
        "expected_direction": None,
        "notes": "Explicitly says Red Line.",
        "pattern": 1,
    },
    {
        "facility_name": "Government Center Elevator 722 (Blue Line platform to Green Line platform)",
        "station_name": "Government Center",
        "routes": ["Blue", "Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_lines": ["Blue", "Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_direction": None,
        "notes": "Explicitly connects Blue Line and Green Line platforms — serves both.",
        "pattern": 1,
    },
    {
        "facility_name": "North Station Elevator 909 (Orange Line, Green Line lobby to Valenti Way)",
        "station_name": "North Station",
        "routes": ["CR-Fitchburg", "CR-Haverhill", "CR-Lowell", "CR-Newburyport", "Green-D", "Green-E", "Orange"],
        "expected_lines": ["Green-D", "Green-E", "Orange"],
        "expected_direction": None,
        "notes": "Explicitly says Orange Line and Green Line. Lobby-to-street, not directional.",
        "pattern": 1,
    },

    # -------------------------------------------------------------------------
    # PATTERN 2 — Terminus references
    # Requires knowing which line terminates at which station.
    # -------------------------------------------------------------------------

    {
        "facility_name": "Shawmut Elevator 953 (Alewife platform to lobby)",
        "station_name": "Shawmut",
        "routes": ["Red"],
        "expected_lines": ["Red"],
        "expected_direction": "northbound",
        "notes": "Alewife is the northern terminus of the Red Line. 'Alewife platform' = the platform "
                 "where trains are heading toward Alewife, i.e., northbound.",
        "pattern": 2,
    },
    {
        "facility_name": "Shawmut Elevator 954 (Ashmont platform to lobby)",
        "station_name": "Shawmut",
        "routes": ["Red"],
        "expected_lines": ["Red"],
        "expected_direction": "southbound",
        "notes": "Ashmont is the southern terminus of the Red Line Ashmont branch. "
                 "'Ashmont platform' = trains heading toward Ashmont, i.e., southbound.",
        "pattern": 2,
    },
    {
        "facility_name": "Sullivan Square Elevator 881 (Oak Grove platform to lobby)",
        "station_name": "Sullivan Square",
        "routes": ["Orange"],
        "expected_lines": ["Orange"],
        "expected_direction": "northbound",
        "notes": "Oak Grove is the northern terminus of the Orange Line.",
        "pattern": 2,
    },
    {
        "facility_name": "Sullivan Square Escalator 307 (Forest Hills platform to paid lobby)",
        "station_name": "Sullivan Square",
        "routes": ["Orange"],
        "expected_lines": ["Orange"],
        "expected_direction": "southbound",
        "notes": "Forest Hills is the southern terminus of the Orange Line.",
        "pattern": 2,
    },
    {
        "facility_name": "JFK/UMass Elevator 830 (Braintree branch platform to lobby)",
        "station_name": "JFK/UMass",
        "routes": ["CR-Greenbush", "CR-Kingston", "CR-NewBedford", "Red"],
        "expected_lines": ["Red"],
        "expected_direction": "southbound",
        "notes": "Braintree is the southern terminus of the Red Line Braintree branch. "
                 "At JFK/UMass the Red Line splits — this platform is the Braintree branch.",
        "pattern": 2,
    },
    {
        "facility_name": "JFK/UMass Elevator 831 (Ashmont branch platform to lobby)",
        "station_name": "JFK/UMass",
        "routes": ["CR-Greenbush", "CR-Kingston", "CR-NewBedford", "Red"],
        "expected_lines": ["Red"],
        "expected_direction": "southbound",
        "notes": "Ashmont is the southern terminus of the Red Line Ashmont branch. "
                 "At JFK/UMass this is the Ashmont-branch platform.",
        "pattern": 2,
    },
    {
        "facility_name": "Ashmont Elevator 968 (Alewife platform to Peabody Square lobby)",
        "station_name": "Ashmont",
        "routes": ["Mattapan", "Red"],
        "expected_lines": ["Red"],
        "expected_direction": "northbound",
        "notes": "Alewife is the northern Red Line terminus. At Ashmont (where Red and Mattapan "
                 "meet), 'Alewife platform' unambiguously means the Red Line northbound platform.",
        "pattern": 2,
    },
    {
        "facility_name": "Wood Island Elevator 889 (Bowdoin platform to pedestrian bridge)",
        "station_name": "Wood Island",
        "routes": ["Blue"],
        "expected_lines": ["Blue"],
        "expected_direction": "westbound",
        "notes": "Bowdoin is the western terminus of the Blue Line.",
        "pattern": 2,
    },

    # -------------------------------------------------------------------------
    # PATTERN 3 — Green Line directional labels
    # 'Park Street & North' = eastbound (inbound toward downtown).
    # 'Kenmore & West' / 'Copley & West' = westbound (outbound).
    # -------------------------------------------------------------------------

    {
        "facility_name": "Arlington Elevator 963 (Park Street & North platform to lobby)",
        "station_name": "Arlington",
        "routes": ["Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_lines": ["Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_direction": "eastbound",
        "notes": "'Park Street & North' is the MBTA label for the eastbound/inbound Green Line "
                 "platform — trains heading toward Park Street and then north toward Lechmere.",
        "pattern": 3,
    },
    {
        "facility_name": "Arlington Elevator 962 (Copley & West platform to lobby)",
        "station_name": "Arlington",
        "routes": ["Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_lines": ["Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_direction": "westbound",
        "notes": "'Copley & West' is the MBTA label for the westbound/outbound Green Line "
                 "platform — trains heading toward Copley and then west on the branches.",
        "pattern": 3,
    },
    {
        "facility_name": "Kenmore Elevator 971 (Westbound platform to lobby)",
        "station_name": "Kenmore",
        "routes": ["Green-B", "Green-C", "Green-D"],
        "expected_lines": ["Green-B", "Green-C", "Green-D"],
        "expected_direction": "westbound",
        "notes": "Kenmore is where Green B, C, D branches split westbound. "
                 "'Westbound platform' serves all three outbound branches.",
        "pattern": 3,
    },
    {
        "facility_name": "Kenmore Elevator 972 (Park Street & North platform to lobby, busway)",
        "station_name": "Kenmore",
        "routes": ["Green-B", "Green-C", "Green-D"],
        "expected_lines": ["Green-B", "Green-C", "Green-D"],
        "expected_direction": "eastbound",
        "notes": "'Park Street & North' = eastbound/inbound. All three branches converge "
                 "inbound at Kenmore before heading toward downtown.",
        "pattern": 3,
    },
    {
        "facility_name": "Copley Elevator 977 (Kenmore & West, Heath Street platform to Boylston Street)",
        "station_name": "Copley",
        "routes": ["Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_lines": ["Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_direction": "westbound",
        "notes": "'Kenmore & West' = westbound/outbound. 'Heath Street' is the Green-E southern terminus, "
                 "also westbound/outbound. This platform serves all four branches heading outbound.",
        "pattern": 3,
    },

    # -------------------------------------------------------------------------
    # PATTERN 4 — Cross-platform connectors (serves two distinct lines)
    # -------------------------------------------------------------------------

    {
        "facility_name": "Downtown Crossing Elevator 998 (Oak Grove platform to Alewife platform)",
        "station_name": "Downtown Crossing",
        "routes": ["Orange", "Red"],
        "expected_lines": ["Orange", "Red"],
        "expected_direction": None,
        "notes": "Oak Grove = Orange Line northbound terminus; Alewife = Red Line northbound terminus. "
                 "This elevator physically connects the two platforms — it serves both lines.",
        "pattern": 4,
    },
    {
        "facility_name": "Downtown Crossing Elevator 869 (Alewife platform to Summer Street Concourse)",
        "station_name": "Downtown Crossing",
        "routes": ["Orange", "Red"],
        "expected_lines": ["Red"],
        "expected_direction": "northbound",
        "notes": "Alewife = Red Line northbound terminus. This elevator is on the Red Line side only — "
                 "Summer Street Concourse is street-level access to the Red Line platform.",
        "pattern": 4,
    },
    {
        "facility_name": "Downtown Crossing Elevator 891 (Oak Grove platform to Franklin Street)",
        "station_name": "Downtown Crossing",
        "routes": ["Orange", "Red"],
        "expected_lines": ["Orange"],
        "expected_direction": "northbound",
        "notes": "Oak Grove = Orange Line northbound terminus. This elevator is on the Orange "
                 "Line side, connecting that platform to street level.",
        "pattern": 4,
    },

    # -------------------------------------------------------------------------
    # PATTERN 5 — Generic / shared infrastructure (not line-specific)
    # Correct answer: serves all lines at the station.
    # -------------------------------------------------------------------------

    {
        "facility_name": "Arlington Elevator 964 (Lobby to Boylston Street)",
        "station_name": "Arlington",
        "routes": ["Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_lines": ["Green-B", "Green-C", "Green-D", "Green-E"],
        "expected_direction": None,
        "notes": "Lobby-to-street elevator. Not associated with either platform direction — "
                 "serves all Green Line passengers at this station.",
        "pattern": 5,
    },
    {
        "facility_name": "North Station Elevator 731 (Passageway to Causeway Street)",
        "station_name": "North Station",
        "routes": ["CR-Fitchburg", "CR-Haverhill", "CR-Lowell", "CR-Newburyport", "Green-D", "Green-E", "Orange"],
        "expected_lines": ["CR-Fitchburg", "CR-Haverhill", "CR-Lowell", "CR-Newburyport", "Green-D", "Green-E", "Orange"],
        "expected_direction": None,
        "notes": "Passageway to street — shared infrastructure serving all lines at the station.",
        "pattern": 5,
    },
    {
        "facility_name": "Forest Hills Elevator 843 (Lower busway to upper lobby)",
        "station_name": "Forest Hills",
        "routes": ["CR-Franklin", "CR-Needham", "Orange"],
        "expected_lines": ["CR-Franklin", "CR-Needham", "Orange"],
        "expected_direction": None,
        "notes": "Busway-to-lobby elevator. Serves the station as a whole, not a specific line.",
        "pattern": 5,
    },
]


if __name__ == "__main__":
    from collections import Counter
    counts = Counter(c["pattern"] for c in GOLDEN)
    print(f"Total cases: {len(GOLDEN)}")
    for p, label in [
        (1, "Explicit line name"),
        (2, "Terminus reference"),
        (3, "Green Line directional"),
        (4, "Cross-platform connector"),
        (5, "Generic/shared"),
    ]:
        print(f"  Pattern {p} ({label}): {counts[p]} cases")
