#!/usr/bin/env bash
# run_timing_prior_14.sh - the 14 tests that were not reproduced, top-10 methods by TIMING PRIOR ONLY (no GPT-2 order), all lines.
# Run from tdrepro/ (venv active). Needs projects/ and metadata/embedings already prepared by run_td_mix_all.sh (or your old folder).
#   DRY_RUN=1 bash run_timing_prior_14.sh                      # print the methods only
#   nohup bash run_timing_prior_14.sh > run_timing_prior.log 2>&1 &
# Env: TOP_K=10  CSV=../data/failed_14_tests.csv
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
TOP_K="${TOP_K:-10}"; CSV="${CSV:-../data/failed_14_tests.csv}"; RES="results/sequential_delay_injection_timingprior_top${TOP_K}.csv"
export TDREPRO_RANKER=timing-prior TOP_K DRY_RUN="${DRY_RUN:-}"
ts(){ date '+%F %T'; }
mkdir -p logs results
sed -i 's/\r$//' "$CSV"
for f in wrapper_for_sequential_delay_injection_tdmix.py td_mix.py timing_api_names.txt "$CSV"; do [ -f "$f" ] || { echo "MISSING: $f"; exit 1; }; done
python3 -c "import torch, transformers, pandas" 2>/dev/null || { echo "pip install torch transformers pandas"; exit 1; }
[ -d projects ] || { echo "projects/ not found - run run_td_mix_all.sh steps 3 first (clones + checkout)"; exit 1; }

echo "[$(ts)] 1) rank lists (timing prior only)"
if [ ! -d timing-prior/ranked ]; then
  EMB=embedings; [ -d metadata/embedings ] && EMB=metadata/embedings
  python3 make_timing_prior_folder.py "$CSV" "$EMB" timing-prior || exit 1
fi
echo "   $(ls timing-prior/ranked | wc -l) ranked files in timing-prior/ranked/"

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

echo "[$(ts)] 4) sweep: timing prior only, top-$TOP_K methods, all lines"
python3 wrapper_for_sequential_delay_injection_tdmix.py "$CSV"
echo "[$(ts)] finished. results: $RES"
cat "$RES" 2>/dev/null
