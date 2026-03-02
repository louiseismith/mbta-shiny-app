# benchmark_facility_interpreter.py
# Tests a facility name interpreter prompt against the golden dataset.
# Measures JSON validity, line accuracy, and direction accuracy per model.
#
# Usage:
#   python benchmark_facility_interpreter.py                              # gemma3:12b via Ollama Cloud
#   python benchmark_facility_interpreter.py --model llama3.1:8b         # different Ollama Cloud model
#   python benchmark_facility_interpreter.py --provider ollama-local --model llama3.2:3b
#   python benchmark_facility_interpreter.py --provider openai --model gpt-4o-mini
#   python benchmark_facility_interpreter.py --provider openai --model gpt-4o

import argparse
import json
import os
import requests
from dotenv import load_dotenv
from facility_interpreter_golden import GOLDEN

load_dotenv()

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert on the MBTA transit network in Boston.
Your job is to interpret the name of an accessibility facility (elevator or escalator)
and identify which transit line(s) and platform direction it serves.

You will be given:
- facility_name: the full name of the facility
- station_name: the station it is located at
- routes: the transit lines that serve this station

Return a JSON object with these fields:
- lines: list of route IDs from the provided routes that this facility specifically serves.
  If the facility serves all lines at the station (e.g. a lobby-to-street elevator),
  return all route IDs. Never include routes not in the provided list.
- direction: the platform direction this facility serves — one of "northbound",
  "southbound", "eastbound", "westbound", or null if the facility is not
  direction-specific (e.g. serves both platforms or is street-level).
- confidence: "high", "medium", or "low"
- reasoning: one sentence explaining your answer

Return only valid JSON. No explanation outside the JSON object."""


def build_user_message(case):
    return json.dumps({
        "facility_name": case["facility_name"],
        "station_name": case["station_name"],
        "routes": case["routes"],
    }, indent=2)


# ---------------------------------------------------------------------------
# Model calls
# ---------------------------------------------------------------------------

def query_ollama_cloud(prompt_messages, model):
    """Call Ollama Cloud (the same endpoint the app uses)."""
    api_key = os.getenv("OLLAMA_API_KEY")
    resp = requests.post(
        "https://ollama.com/api/chat",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": prompt_messages,
            "stream": False,
            "options": {"num_predict": 300},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def query_ollama_local(prompt_messages, model):
    """Call a locally running Ollama instance."""
    resp = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "messages": prompt_messages,
            "stream": False,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def query_openai(prompt_messages, model):
    """Call OpenAI chat completions API."""
    api_key = os.getenv("OPENAI_API_KEY")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": prompt_messages,
            "max_tokens": 300,
            "response_format": {"type": "json_object"},  # enforces JSON output
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def call_model(case, model, provider):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(case)},
    ]
    if provider == "openai":
        return query_openai(messages, model)
    elif provider == "ollama-local":
        return query_ollama_local(messages, model)
    else:  # ollama-cloud
        return query_ollama_cloud(messages, model)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def normalize_direction(d):
    """Normalize 'northbound'/'north'/'North' → 'northbound', etc."""
    if d is None:
        return None
    d = d.lower().strip()
    for canonical in ("northbound", "southbound", "eastbound", "westbound"):
        if d == canonical or d == canonical.replace("bound", ""):
            return canonical
    return d


def score(case, raw_output):
    """Parse model output and score it against expected values."""
    result = {
        "json_valid": False,
        "lines_correct": False,
        "direction_correct": False,
        "parsed": None,
        "raw": raw_output,
        "error": None,
    }

    # Parse JSON — strip markdown fences if present
    text = raw_output.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        parsed = json.loads(text)
        result["json_valid"] = True
        result["parsed"] = parsed
    except json.JSONDecodeError as e:
        result["error"] = f"JSON parse error: {e}"
        return result

    # Score lines
    got_lines = sorted(parsed.get("lines", []))
    expected_lines = sorted(case["expected_lines"])
    result["lines_correct"] = got_lines == expected_lines

    # Score direction
    got_dir = normalize_direction(parsed.get("direction"))
    exp_dir = normalize_direction(case["expected_direction"])
    result["direction_correct"] = got_dir == exp_dir

    return result


# ---------------------------------------------------------------------------
# Main benchmark loop
# ---------------------------------------------------------------------------

PATTERN_LABELS = {
    1: "Explicit line name",
    2: "Terminus reference",
    3: "Green Line directional",
    4: "Cross-platform connector",
    5: "Generic/shared",
}


def run_benchmark(model, provider):
    print(f"\nModel: {model} (provider: {provider})")
    print("=" * 80)

    results = []
    for i, case in enumerate(GOLDEN, 1):
        print(f"  [{i:02d}/{len(GOLDEN)}] {case['facility_name'][:60]}...", end=" ", flush=True)
        try:
            raw = call_model(case, model, provider)
            scored = score(case, raw)
        except Exception as e:
            scored = {
                "json_valid": False, "lines_correct": False,
                "direction_correct": False, "parsed": None,
                "raw": "", "error": str(e),
            }
        scored["case"] = case
        results.append(scored)

        status = []
        if not scored["json_valid"]:
            status.append("INVALID JSON")
        else:
            status.append("lines OK" if scored["lines_correct"] else "lines WRONG")
            status.append("dir OK" if scored["direction_correct"] else "dir WRONG")
        print(" | ".join(status))

    # Summary by pattern
    print(f"\n{'='*80}")
    print("RESULTS BY PATTERN")
    print(f"{'='*80}")
    print(f"  {'PATTERN':<32} {'JSON':>5} {'LINES':>6} {'DIR':>5} {'ALL':>5}")
    print(f"  {'-'*57}")

    for p in sorted(PATTERN_LABELS):
        subset = [r for r in results if r["case"]["pattern"] == p]
        if not subset:
            continue
        n = len(subset)
        json_ok = sum(r["json_valid"] for r in subset)
        lines_ok = sum(r["lines_correct"] for r in subset)
        dir_ok = sum(r["direction_correct"] for r in subset)
        all_ok = sum(r["json_valid"] and r["lines_correct"] and r["direction_correct"] for r in subset)
        label = f"P{p}: {PATTERN_LABELS[p]}"
        print(f"  {label:<32} {json_ok}/{n:>2}  {lines_ok}/{n:>2}  {dir_ok}/{n:>2}  {all_ok}/{n:>2}")

    # Overall
    n = len(results)
    json_ok = sum(r["json_valid"] for r in results)
    lines_ok = sum(r["lines_correct"] for r in results)
    dir_ok = sum(r["direction_correct"] for r in results)
    all_ok = sum(r["json_valid"] and r["lines_correct"] and r["direction_correct"] for r in results)
    print(f"  {'-'*57}")
    print(f"  {'OVERALL':<32} {json_ok}/{n:>2}  {lines_ok}/{n:>2}  {dir_ok}/{n:>2}  {all_ok}/{n:>2}")

    # Show failures in detail
    failures = [r for r in results if not (r["json_valid"] and r["lines_correct"] and r["direction_correct"])]
    if failures:
        print(f"\n{'='*80}")
        print("FAILURES")
        print(f"{'='*80}")
        for r in failures:
            case = r["case"]
            print(f"\n  P{case['pattern']} | {case['facility_name']}")
            print(f"  Expected: lines={case['expected_lines']}, direction={case['expected_direction']}")
            if not r["json_valid"]:
                print(f"  Error: {r['error']}")
                print(f"  Raw output: {r['raw'][:200]}")
            else:
                p = r["parsed"]
                print(f"  Got:      lines={p.get('lines')}, direction={p.get('direction')}")
                print(f"  Reasoning: {p.get('reasoning', '')}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemma3:12b", help="Model name")
    parser.add_argument(
        "--provider",
        default="ollama-cloud",
        choices=["ollama-cloud", "ollama-local", "openai"],
        help="Which API to use (default: ollama-cloud)",
    )
    args = parser.parse_args()

    run_benchmark(args.model, args.provider)
