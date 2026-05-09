"""hw3_experiment.py

Generates MBTA accessibility briefings using three prompt variants, validates
each with Claude Haiku, and performs statistical analysis (ANOVA + t-tests).

Run from the homework/shiny_app/ directory:
    python scripts/hw3_experiment.py

Outputs:
    data/hw3_scores.csv   — raw scores (one row per trial)
    data/hw3_chart.png    — bar chart with 95% CIs by prompt variant
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

import time
import threading

from modules.ai_report import _format_duration, _query_ollama

matplotlib.use("Agg")
load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FIXTURES_PATH = os.path.join(DATA_DIR, "hw3_fixtures.json")
SCORES_PATH = os.path.join(DATA_DIR, "hw3_scores.csv")
CHART_PATH = os.path.join(DATA_DIR, "hw3_chart.png")

RUNS_PER_CELL = 20  # runs per (station × prompt) → 120 scores per prompt variant

# Rate limiter for Ollama Cloud (50 req/min hard limit; target 40 to leave headroom)
_ollama_lock = threading.Lock()
_ollama_last_call = 0.0
_OLLAMA_MIN_INTERVAL = 60.0 / 40  # 1.5s between calls


def _query_ollama_rate_limited(prompt):
    global _ollama_last_call
    with _ollama_lock:
        wait = _OLLAMA_MIN_INTERVAL - (time.time() - _ollama_last_call)
        if wait > 0:
            time.sleep(wait)
        _ollama_last_call = time.time()
    return _query_ollama(prompt)

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


def validate_report(data_block, briefing):
    resp = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        system="You are evaluating AI-generated transit accessibility briefings. Follow the rubric exactly and return only JSON.",
        messages=[{"role": "user", "content": (
            f"Source data the briefing was generated from:\n{data_block}\n"
            f"Briefing to evaluate:\n{briefing}\n\n"
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

def run_trial(fixture, prompt_label, run_num, max_retries=3):
    name = fixture["station_name"]
    facilities = fixture["facilities"]
    service_alerts = fixture.get("service_alerts", [])
    wheelchair_boarding = fixture.get("wheelchair_boarding", 0)

    data_block = _build_data_block(name, facilities, service_alerts, wheelchair_boarding)
    prompt = PROMPTS[prompt_label](name, facilities, service_alerts, wheelchair_boarding)

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            briefing = _query_ollama_rate_limited(prompt)
            scores = validate_report(data_block, briefing)
            return {
                "station": name,
                "case": fixture.get("case", "unknown"),
                "prompt": prompt_label,
                "run": run_num,
                "briefing": briefing,
                **scores,
            }
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                print(f"    retry {attempt}/{max_retries - 1} for Prompt {prompt_label} | {name} run {run_num}: {e}")
    raise last_exc


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

def plot_bar_ci(df):
    import numpy as np

    dims = ["scannability", "clarity", "actionability"]
    prompt_labels = ["A\n(prose)", "B\n(bullets)", "C\n(bullets\n+ impact)"]
    colors = ["#d9e8f5", "#a8c8e8", "#4a90c4"]
    x = np.arange(len(dims))
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle("MBTA Briefing Quality by Prompt Variant", fontsize=14, fontweight="bold")

    for i, (prompt, label, color) in enumerate(zip(["A", "B", "C"], prompt_labels, colors)):
        means, cis = [], []
        for dim in dims:
            vals = df[df["prompt"] == prompt][dim].values
            mean = vals.mean()
            se = vals.std() / len(vals) ** 0.5
            means.append(mean)
            cis.append(1.96 * se)
        bars = ax.bar(x + i * width, means, width, label=label, color=color,
                      edgecolor="gray", linewidth=0.5)
        ax.errorbar(x + i * width, means, yerr=cis, fmt="none",
                    color="black", capsize=4, linewidth=1.2)

    ax.set_xticks(x + width)
    ax.set_xticklabels([d.capitalize() for d in dims])
    ax.set_ylabel("Mean Score (1–5) with 95% CI")
    ax.set_ylim(0, 5.5)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.legend(title="Prompt", bbox_to_anchor=(1.01, 1), loc="upper left")
    ax.grid(axis="y", alpha=0.3, linestyle="--")

    plt.tight_layout()
    plt.savefig(CHART_PATH, dpi=150, bbox_inches="tight")
    print(f"Chart saved → {CHART_PATH}")


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
    plot_bar_ci(df)
    write_samples(df)
    print("Done.")


if __name__ == "__main__":
    main()
