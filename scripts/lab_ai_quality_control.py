# lab_ai_quality_control.py
# AI Quality Control — MBTA Accessibility Reports
#
# Reads all reports from sample_reports.txt, scores each on three criteria,
# and prints a comparison table across conditions.
#
# Criteria:
#   accurate        (boolean) — no factual errors about facility statuses
#   verdict_clarity (1-5)     — rider can immediately tell if they can get through
#   actionability   (1-5)     — when something is out, gives specific guidance
#
# Run from: homework/shiny_app/
#   python scripts/lab_ai_quality_control.py

import json
import os
import re
import time

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL   = "gpt-4o-mini"

REPORTS_PATH     = "data/lab_reports/sample_reports.txt"
SOURCE_DATA_PATH = "data/lab_reports/mbta_source_data.txt"

# ---------------------------------------------------------------------------
# Parse reports file
# Returns list of (label, report_text) tuples
# ---------------------------------------------------------------------------

def load_reports(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    reports = []
    for block in text.split("\n\n---\n\n"):
        block = block.strip()
        if not block:
            continue
        header, _, body = block.partition("\n")
        label = header.strip("# ").strip()
        reports.append((label, body.strip()))
    return reports

# ---------------------------------------------------------------------------
# QC prompt
# ---------------------------------------------------------------------------

def build_qc_prompt(report_text, source_data):
    return f"""You are a quality control validator for AI-generated MBTA accessibility briefings.
These briefings are shown to riders who rely on elevators or escalators to navigate stations.

Source Data (ground truth — facility statuses for this station):
{source_data}

Report to Validate:
{report_text}

Evaluate the report on three criteria and return valid JSON:

1. accurate (boolean): Does the report correctly represent the status of all facilities
   in the source data? true = no factual errors, false = any status is wrong or missing.

2. verdict_clarity (1-5): Can a rider immediately understand whether the station is
   accessible from street to platform?
   1 = bottom line is absent or buried; 5 = stated immediately and unambiguously.

3. actionability (1-5): When facilities are out of service, does the report give the
   rider specific, useful guidance drawing on the MBTA instructions in the source data?
   1 = no guidance despite outages; 5 = clear specific alternatives for each outage.
   If all facilities are operational, return 5.

Return only this JSON, nothing else:
{{
  "accurate": true or false,
  "verdict_clarity": 1-5,
  "actionability": 1-5,
  "details": "one sentence explanation"
}}"""

# ---------------------------------------------------------------------------
# Query OpenAI
# ---------------------------------------------------------------------------

def score_report(report_text, source_data):
    prompt = build_qc_prompt(report_text, source_data)
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        },
    )
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]
    data = json.loads(raw)
    return {
        "accurate":       data["accurate"],
        "verdict_clarity": data["verdict_clarity"],
        "actionability":  data["actionability"],
        "overall":        round((data["verdict_clarity"] + data["actionability"]) / 2, 2),
        "details":        data["details"],
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    reports    = load_reports(REPORTS_PATH)
    with open(SOURCE_DATA_PATH, "r", encoding="utf-8") as f:
        source_data = f.read()

    print(f"Source data:\n{source_data}\n")
    print(f"Scoring {len(reports)} reports...\n")

    rows = []
    for label, report_text in reports:
        print(f"  Scoring: {label} ...", end=" ", flush=True)
        scores = score_report(report_text, source_data)
        rows.append({"condition": label, **scores})
        print(f"overall={scores['overall']}")
        time.sleep(0.5)

    df = pd.DataFrame(rows)

    print("\n" + "=" * 80)
    print("QUALITY CONTROL RESULTS")
    print("=" * 80)

    for _, row in df.iterrows():
        print(f"\n[{row['condition']}]")
        print(f"  Accurate:       {'✅ PASS' if row['accurate'] else '❌ FAIL'}")
        print(f"  Verdict clarity: {row['verdict_clarity']} / 5")
        print(f"  Actionability:   {row['actionability']} / 5")
        print(f"  Overall:         {row['overall']} / 5")
        print(f"  Notes: {row['details']}")

    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    header = f"{'Condition':<6}  {'Accurate':<10}  {'Verdict Clarity':<17}  {'Actionability':<15}  {'Overall'}"
    print(header)
    print("-" * len(header))
    for _, row in df.iterrows():
        cond = row["condition"].split(":")[0].strip()  # just "A", "B", "C", "D"
        acc  = "✅ PASS" if row["accurate"] else "❌ FAIL"
        print(f"{cond:<6}  {acc:<10}  {row['verdict_clarity']:<17}  {row['actionability']:<15}  {row['overall']}")


if __name__ == "__main__":
    main()
