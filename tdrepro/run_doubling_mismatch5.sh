#!/usr/bin/env bash
# run_doubling_mismatch5.sh - the 5 "failure found, log mismatch" tests, timing-prior top-10 methods, every line, delay 100 ms doubling up to 25 s.
# Run from tdrepro/ (venv active). projects/ and metadata/embedings/ must already exist (from run_td_mix_all.sh / run_uisample_timing.sh).
#   DRY_RUN=1 bash run_doubling_mismatch5.sh                 # builds the rank lists and prints methods/lines, runs nothing
#   nohup bash run_doubling_mismatch5.sh > run_doubling.log 2>&1 &
# Env: CSV=../data/log_mismatch_5_tests.csv  TOP_K=10  MISMATCH=stop|continue  ONLY=56,621  (the rest: see doubling_delay_sweep.py)
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
CSV="${CSV:-../data/log_mismatch_5_tests.csv}"; TOP_K="${TOP_K:-10}"; export TOP_K PRIOR_DIR=timing-prior-mismatch
ts(){ date '+%F %T'; }
mkdir -p logs results
sed -i 's/\r$//' "$CSV"
for f in doubling_delay_sweep.py make_timing_prior_folder.py td_mix.py timing_api_names.txt run_test.sh modify_java_file.py utils.py "$CSV"; do [ -f "$f" ] || { echo "MISSING: $f"; exit 1; }; done
python3 -c "import pandas" 2>/dev/null || { echo "pip install pandas"; exit 1; }
[ -d projects ] || { echo "projects/ not found"; exit 1; }

echo "[$(ts)] 1) rank lists (timing prior only) -> $PRIOR_DIR/"
EMB=embedings; [ -d metadata/embedings ] && EMB=metadata/embedings
python3 make_timing_prior_folder.py "$CSV" "$EMB" "$PRIOR_DIR" || exit 1

echo "[$(ts)] 2) baseline failure logs (create the missing ones)"
[ -f ../Results/merged_failures.csv ] || (cd ../Results && unzip -o merged_failures.zip >/dev/null)
tail -n +2 "$CSV" | while IFS=, read -r id slug sha module test; do
  [ -z "$id" ] && continue
  proj="${slug#*/}"; mdot="${module//\//.}"
  if [ "$module" = "." ]; then out="logs/${id}_${proj}_${test}_stacktrace.csv"; else out="logs/${id}_${mdot}_${test}_stacktrace.csv"; fi
  [ -s "$out" ] && continue
  python3 find_failure_message_and_save.py "$id" "$slug" "$sha" "$module" "$test" ../Results/merged_failures.csv "$mdot" "$proj" 2>&1 | grep -E "No matching|Error|Traceback"
  [ -s "$out" ] || echo "WARNING: baseline log missing for id=$id - that test will be skipped"
done

echo "[$(ts)] 3) leftover injected sleeps"
for d in projects/*/*/; do
  [ -d "$d/.git" ] || continue
  n=$(git -C "$d" diff -U0 -- '*.java' | grep -c '^+.*Thread.sleep(')
  [ "${n:-0}" -gt 0 ] && { echo "restoring $n leftover sleep(s) in $d"; git -C "$d" checkout -- '*.java'; }   # never 'git checkout -- .'
done

echo "[$(ts)] 4) doubling-delay sweep"
python3 doubling_delay_sweep.py "$CSV"
echo "[$(ts)] finished. results/doubling_delay_sweep.csv  results/doubling_runs.csv  results/doubling_summary.csv"
