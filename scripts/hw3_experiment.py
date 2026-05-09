"""hw3_experiment.py

Generates MBTA accessibility briefings using three prompt variants, validates
each with Claude Haiku, and performs statistical analysis (ANOVA + t-tests).

Run from the homework/shiny_app/ directory:
    python scripts/hw3_experiment.py

Outputs:
    data/hw3_scores.csv     — raw scores (one row per trial)
    data/hw3_boxplot.png    — boxplot of scores by prompt variant
"""

import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
from dotenv import load_dotenv
from scipy import stats

from modules.ai_report import _format_duration, _query_ollama

matplotlib.use("Agg")
load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FIXTURES_PATH = os.path.join(DATA_DIR, "hw3_fixtures.json")
SCORES_PATH = os.path.join(DATA_DIR, "hw3_scores.csv")
BOXPLOT_PATH = os.path.join(DATA_DIR, "hw3_boxplot.png")

RUNS_PER_CELL = 5  # runs per (station × prompt) → 30 scores per prompt variant

# ── Data block (shared across all prompt variants) ───────────────────────────

_TYPE_LABELS = {
    "ELEVATOR": "elevator",
    "ESCALATOR": "escalator",
    "PORTABLE_BOARDING_LIFT": "portable lift",
}


def _build_data_block(station_name, facilities, service_alerts, wheelchair_boarding):
    counts, out_counts = {}, {}
    for f in facilities:
        t = f.get("type", "UNKNOWN")
        counts[t] = counts.get(t, 0) + 1
        if f.get("status") == "out_of_service":
            out_counts[t] = out_counts.get(t, 0) + 1

    parts = []
    for t in ("ELEVATOR", "ESCALATOR", "PORTABLE_BOARDING_LIFT"):
        total = counts.get(t, 0)
        if not total:
            continue
        out = out_counts.get(t, 0)
        label = _TYPE_LABELS.get(t, t.lower())
        parts.append(f"{label}s: {total} ({out} out)" if total != 1 else f"{label}: 1 ({out} out)")
    summary = " | ".join(parts) if parts else "No facilities on record"

    wheelchair_line = ""
    if wheelchair_boarding == 2:
        wheelchair_line = (
            "STATION WHEELCHAIR STATUS: NOT ACCESSIBLE — this station is "
            "not wheelchair accessible per GTFS data.\n\n"
        )

    operational = [f for f in facilities if f.get("status") == "operational"]
    out_of_service = [f for f in facilities if f.get("status") == "out_of_service"]

    fac_lines = []
    for f in out_of_service:
        name = f.get("name") or f.get("short_name") or ""
        line = f"- {f['type']} \"{name}\": OUT OF SERVICE"
        alert = f.get("alert")
        if alert:
            duration = _format_duration(alert.get("outage_start"))
            if duration:
                line += f" (down {duration})"
            if alert.get("cause"):
                line += f"\n  Cause: {alert['cause']}"
            if alert.get("header"):
                line += f"\n  Alert: {alert['header']}"
            desc = (alert.get("description") or "").strip()
            if desc:
                line += f"\n  MBTA instructions: {desc}"
        fac_lines.append(line)

    if len(operational) > 6:
        by_type = {}
        for f in operational:
            t = f.get("type", "UNKNOWN")
            by_type[t] = by_type.get(t, 0) + 1
        fac_lines.append("- " + ", ".join(
            f"{n} {_TYPE_LABELS.get(t, t.lower())}(s)" for t, n in sorted(by_type.items())
        ) + " operational")
    else:
        for f in operational:
            name = f.get("name") or f.get("short_name") or ""
            fac_lines.append(f"- {f['type']} \"{name}\": OPERATIONAL")

    facilities_block = "\n".join(fac_lines) if fac_lines else "(none)"

    if service_alerts:
        alert_lines = []
        for sa in service_alerts:
            line = f"- [{sa['effect']}] {sa['header']}"
            if sa.get("description"):
                line += f"\n  Details: {sa['description'][:300]}"
            alert_lines.append(line)
        service_block = "\n".join(alert_lines)
    else:
        service_block = "(none)"

    return (
        f"You are generating an accessibility briefing for {station_name} station "
        f"for riders who may rely on elevators, escalators, or other accessibility "
        f"facilities to navigate the station. Use the data below.\n\n"
        f"{wheelchair_line}"
        f"{summary}\n\n"
        f"Facilities:\n{facilities_block}\n\n"
        f"Service alerts:\n{service_block}\n\n"
    )


# ── Prompt variants ───────────────────────────────────────────────────────────

def prompt_a(station_name, facilities, service_alerts, wheelchair_boarding):
    """Baseline: two prose paragraphs (current production prompt)."""
    return _build_data_block(station_name, facilities, service_alerts, wheelchair_boarding) + (
        "In one short paragraph (1-3 sentences):\n"
        "1. State whether the station is currently accessible from street to platform.\n"
        "2. If not, what riders should do instead — use specific details from "
        "the MBTA instructions above.\n"
        "In another short paragraph (1-2 sentences):\n"
        "1. Note any service disruptions that may affect travel through this station.\n\n"
        "Only use the data above. Write directly and concisely — "
        "no greeting or preamble. Start with the key information immediately."
    )


def prompt_b(station_name, facilities, service_alerts, wheelchair_boarding):
    """Bullets: same content, bullet format instead of paragraphs."""
    return _build_data_block(station_name, facilities, service_alerts, wheelchair_boarding) + (
        "Respond with exactly two bullet points — no paragraphs, no preamble, no headers:\n"
        "• Accessibility status: is the station accessible street-to-platform? "
        "If not, what should riders do?\n"
        "• Service disruptions: any route-level disruptions affecting travel through "
        "this station? If none, state that briefly.\n\n"
        "Only use the data above."
    )


def prompt_c(station_name, facilities, service_alerts, wheelchair_boarding):
    """Bullets + impact language: bullets and plain-English impact descriptions."""
    return _build_data_block(station_name, facilities, service_alerts, wheelchair_boarding) + (
        "Respond with exactly two bullet points — no paragraphs, no preamble, no headers:\n"
        "• Accessibility status: is the station accessible street-to-platform? "
        "If not, what should riders do? Describe what the rider will experience — "
        "avoid citing internal route codes or numbers unless you explain what they mean.\n"
        "• Service disruptions: any route-level disruptions affecting travel through "
        "this station? Describe the real-world impact (e.g., 'trains are not running; "
        "take a replacement bus'). If none, state that briefly.\n\n"
        "Only use the data above."
    )


PROMPTS = {"A": prompt_a, "B": prompt_b, "C": prompt_c}

# ── Validator ─────────────────────────────────────────────────────────────────

_client = anthropic.Anthropic()

_RUBRIC = """Score this MBTA accessibility briefing on three dimensions. Return ONLY valid JSON — no other text.

Scannability — ease of parsing at a glance in a visual UI:
  1 = dense prose paragraph(s), hard to scan quickly
  3 = some structure but still paragraph-heavy
  5 = fully structured (clear bullets or visual breaks), easy to scan

Clarity — communicates impact without requiring local Boston transit knowledge:
  1 = opaque route codes / jargon, reader left confused
  3 = mostly clear with minor unexplained references
  5 = anyone could immediately understand what this means for their trip

Actionability — tells a rider what to do if something is broken:
  1 = describes the problem only, no guidance
  3 = vague suggestion (e.g., "seek alternatives")
  5 = specific, usable instruction the rider can act on

Return exactly: {"scannability": N, "clarity": N, "actionability": N}"""


def validate_report(fixture, briefing):
    out_count = sum(1 for f in fixture["facilities"] if f.get("status") == "out_of_service")
    alert_count = len(fixture.get("service_alerts", []))
    context = (
        f"Station: {fixture['station_name']}\n"
        f"Facilities out of service: {out_count}\n"
        f"Service alerts active: {alert_count}\n"
        f"Wheelchair accessible: {'No' if fixture.get('wheelchair_boarding') == 2 else 'Yes'}"
    )

    resp = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        system="You are evaluating AI-generated transit accessibility briefings. Follow the rubric exactly and return only JSON.",
        messages=[{"role": "user", "content": (
            f"Station context:\n{context}\n\n"
            f"Briefing:\n{briefing}\n\n"
            f"{_RUBRIC}"
        )}],
    )
    text = resp.content[0].text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    return json.loads(text)


# ── Experiment runner ─────────────────────────────────────────────────────────

def run_trial(fixture, prompt_label, run_num):
    builder = PROMPTS[prompt_label]
    prompt = builder(
        fixture["station_name"],
        fixture["facilities"],
        fixture.get("service_alerts", []),
        fixture.get("wheelchair_boarding", 0),
    )
    briefing = _query_ollama(prompt)
    scores = validate_report(fixture, briefing)
    return {
        "station": fixture["station_name"],
        "case": fixture.get("case", "unknown"),
        "prompt": prompt_label,
        "run": run_num,
        "briefing": briefing,
        **scores,
    }


def run_experiment(fixtures):
    tasks = [
        (fixture, label, run)
        for fixture in fixtures
        for label in PROMPTS
        for run in range(1, RUNS_PER_CELL + 1)
    ]
    total = len(tasks)
    print(f"\nRunning {total} trials ({len(fixtures)} stations × 3 prompts × {RUNS_PER_CELL} runs)")
    print("─" * 60)

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(run_trial, fixture, label, run): (fixture["station_name"], label, run)
            for fixture, label, run in tasks
        }
        for i, future in enumerate(as_completed(futures), 1):
            station, label, run = futures[future]
            try:
                row = future.result()
                results.append(row)
                s = row
                print(
                    f"  [{i:>3}/{total}] Prompt {label} | {station[:30]:<30} run {run}"
                    f" → scan={s['scannability']} clarity={s['clarity']} action={s['actionability']}"
                )
            except Exception as e:
                print(f"  [{i:>3}/{total}] FAILED Prompt {label} | {station} run {run}: {e}")

    return results


# ── Statistics ────────────────────────────────────────────────────────────────

def run_statistics(df):
    dims = ["scannability", "clarity", "actionability"]
    print("\n" + "=" * 60)
    print("STATISTICAL ANALYSIS")
    print("=" * 60)

    for dim in dims:
        print(f"\n── {dim.upper()} ──")
        groups = {p: df[df["prompt"] == p][dim].values for p in ["A", "B", "C"]}
        for label, g in groups.items():
            print(f"  Prompt {label} (n={len(g)}): mean={g.mean():.2f}, SD={g.std():.2f}")

        f_stat, p_val = stats.f_oneway(*groups.values())
        sig = "*** SIGNIFICANT" if p_val < 0.05 else "(not significant)"
        print(f"  One-way ANOVA: F={f_stat:.3f}, p={p_val:.4f}  {sig}")

        if p_val < 0.05:
            pairs = [("A", "B"), ("A", "C"), ("B", "C")]
            alpha_bonf = 0.05 / len(pairs)
            for p1, p2 in pairs:
                t, tp = stats.ttest_ind(groups[p1], groups[p2])
                marker = " ***" if tp < alpha_bonf else (" *" if tp < 0.05 else "")
                print(f"    t-test {p1} vs {p2}: t={t:.3f}, p={tp:.4f}{marker}")

    print()


# ── Visualization ─────────────────────────────────────────────────────────────

def plot_boxplots(df):
    dims = ["scannability", "clarity", "actionability"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 5), sharey=True)
    fig.suptitle("MBTA Briefing Quality by Prompt Variant", fontsize=14, fontweight="bold")

    colors = ["#d9e8f5", "#a8c8e8", "#4a90c4"]
    labels = ["A\n(prose)", "B\n(bullets)", "C\n(bullets\n+ impact)"]

    for ax, dim in zip(axes, dims):
        data = [df[df["prompt"] == p][dim].values for p in ["A", "B", "C"]]
        bp = ax.boxplot(data, labels=labels, patch_artist=True, widths=0.5)
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.85)
        ax.set_title(dim.capitalize(), fontweight="bold", pad=8)
        ax.set_ylim(0.5, 5.5)
        ax.set_yticks([1, 2, 3, 4, 5])
        if dim == "scannability":
            ax.set_ylabel("Score (1–5)")
        ax.grid(axis="y", alpha=0.3, linestyle="--")

    plt.tight_layout()
    plt.savefig(BOXPLOT_PATH, dpi=150, bbox_inches="tight")
    print(f"Boxplot saved → {BOXPLOT_PATH}")


# ── Sample output ────────────────────────────────────────────────────────────

SAMPLES_PATH = os.path.join(DATA_DIR, "hw3_samples.txt")


def write_samples(df):
    """Write one representative briefing per prompt variant to a readable text file."""
    lines = ["SAMPLE BRIEFINGS — one per prompt variant\n" + "=" * 60 + "\n"]
    for label in ["A", "B", "C"]:
        subset = df[df["prompt"] == label].copy()
        # Pick the row closest to the mean composite score
        subset["composite"] = subset[["scannability", "clarity", "actionability"]].mean(axis=1)
        mean_val = subset["composite"].mean()
        row = subset.iloc[(subset["composite"] - mean_val).abs().argsort().iloc[0]]
        lines.append(
            f"PROMPT {label}  |  {row['station']}  |  case: {row['case']}\n"
            f"scores: scannability={row['scannability']}  clarity={row['clarity']}  actionability={row['actionability']}\n"
            f"{'─' * 60}\n"
            f"{row['briefing']}\n"
        )
    with open(SAMPLES_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"Sample briefings saved → {SAMPLES_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(FIXTURES_PATH):
        print(f"ERROR: Fixtures not found at {FIXTURES_PATH}")
        print("Run scripts/hw3_snapshot.py first.")
        sys.exit(1)

    with open(FIXTURES_PATH) as f:
        fixtures = json.load(f)

    print(f"Loaded {len(fixtures)} fixture stations:")
    for fx in fixtures:
        out = sum(1 for fac in fx["facilities"] if fac.get("status") == "out_of_service")
        alerts = len(fx.get("service_alerts", []))
        print(f"  [{fx.get('case', '?'):20s}] {fx['station_name']} — {out} outage(s), {alerts} alert(s)")

    results = run_experiment(fixtures)

    if not results:
        print("No results collected — exiting.")
        sys.exit(1)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCORES_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"\nRaw scores saved → {SCORES_PATH}")

    df = pd.DataFrame(results)
    run_statistics(df)
    plot_boxplots(df)
    write_samples(df)
    print("Done.")


if __name__ == "__main__":
    main()
