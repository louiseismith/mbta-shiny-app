#!/bin/bash
# run_benchmarks.sh
# Runs the facility interpreter benchmark against all candidate models.
# Results saved to benchmark_results/ with unique filenames.

PYTHON=../../.venv/bin/python
SCRIPT=benchmark_facility_interpreter.py
OUTDIR=benchmark_results

echo "Starting benchmark run — $(date)"
echo "Results will be saved to $OUTDIR/"
echo ""

run() {
    local model=$1
    local provider=$2
    # Make a filesystem-safe filename: replace : / with -
    local safe_model=$(echo "$model" | tr ':/' '--')
    local outfile="$OUTDIR/${safe_model}_${provider}.txt"
    echo ">>> $model ($provider) → $outfile"
    $PYTHON $SCRIPT --model "$model" --provider "$provider" 2>&1 | tee "$outfile"
    echo ""
}

# --- Ollama Cloud ---
run gemma3:4b               ollama-cloud
run gemma3:12b              ollama-cloud
run gemma3:27b              ollama-cloud
run mistral-large-3         ollama-cloud
run gemini-3-flash-preview  ollama-cloud
run deepseek-v3.2           ollama-cloud

# --- OpenAI ---
run gpt-4o-mini     openai
run gpt-4o          openai

echo "Done — $(date)"
echo "Results in $OUTDIR/:"
ls -1 $OUTDIR/
