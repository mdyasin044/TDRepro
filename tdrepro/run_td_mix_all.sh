#!/usr/bin/env bash
# run_td_mix_all.sh - ONE script: setup + baseline logs + TD-Mix sweep on all 21 tests + results CSV.
# Run from the tdrepro folder of a fresh clone (venv active):
#   nohup bash run_td_mix_all.sh > run_all.log 2>&1 &
# Options (env vars):  DRY_RUN=1 (only print the methods)   RANKER=gpt2 (authors' ranking)   TOP_K=10
#                      CSV=../data/failed_21_tests.csv
# Safe to re-run: finished tests are skipped, existing clones/logs are kept.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
RANKER="${RANKER:-td-mix}"; TOP_K="${TOP_K:-10}"; CSV="${CSV:-../data/failed_21_tests.csv}"
WRAPPER=wrapper_for_sequential_delay_injection_tdmix.py
export TDREPRO_RANKER="$RANKER" TOP_K DRY_RUN="${DRY_RUN:-}"
RES="results/sequential_delay_injection_${RANKER//-/}_top${TOP_K}.csv"
ts(){ date '+%F %T'; }
mkdir -p logs results projects

echo "[$(ts)] 1) files check"
for f in td_mix.py timing_api_names.txt $WRAPPER "$CSV"; do [ -f "$f" ] || { echo "MISSING: $f"; exit 1; }; done
sed -i 's/\r$//' "$CSV"
python3 -c "import torch, transformers, pandas" 2>/dev/null || { echo "Install deps first: pip install torch transformers pandas"; exit 1; }

echo "[$(ts)] 2) failure table + embeddings folder"
[ -f ../Results/merged_failures.csv ] || (cd ../Results && unzip -o merged_failures.zip >/dev/null)
[ -f ../Results/merged_failures.csv ] || { echo "merged_failures.csv not found (check unzip output in ../Results)"; exit 1; }
mkdir -p metadata
[ -e metadata/embedings ] || ln -s ../embedings metadata/embedings

echo "[$(ts)] 3) clone projects at the right commit"
tail -n +2 "$CSV" | while IFS=, read -r id slug sha module test; do
  [ -z "$id" ] && continue
  d="projects/$slug"
  [ -d "$d/.git" ] || git clone "https://github.com/$slug" "$d" || { echo "CLONE FAILED $slug"; continue; }
  git -C "$d" checkout -- '*.java' 2>/dev/null          # never 'git checkout -- .'
  git -C "$d" checkout -q "$sha" || echo "CHECKOUT FAILED $slug $sha"
  if [ "$slug" = "apache/httpcore" ] && ! grep -q "surefire-plugin" "$d/pom.xml"; then   # same fix as authors' runAll.sh
    ( cd "$d" && sed -i '/<build>/,/<\/build>/ { /<plugins>/a\
 <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>2.22.1</version></plugin>
 }' pom.xml )
  fi
done

echo "[$(ts)] 4) baseline failure logs"
tail -n +2 "$CSV" | while IFS=, read -r id slug sha module test; do
  [ -z "$id" ] && continue
  proj="${slug#*/}"; mdot="${module//\//.}"
  if [ "$module" = "." ]; then out="logs/${id}_${proj}_${test}_stacktrace.csv"; else out="logs/${id}_${mdot}_${test}_stacktrace.csv"; fi
  [ -s "$out" ] && continue
  python3 find_failure_message_and_save.py "$id" "$slug" "$sha" "$module" "$test" ../Results/merged_failures.csv "$mdot" "$proj" 2>&1 | grep -E "No matching|Error|Traceback"
  [ -s "$out" ] || echo "WARNING: baseline log missing for id=$id ($out) - this test will be skipped"
done
echo "baseline logs: $(ls logs/*_stacktrace.csv 2>/dev/null | wc -l) / $(( $(wc -l < "$CSV") - 1 ))"

echo "[$(ts)] 5) leftover injected sleeps"
for d in projects/*/*/; do
  [ -d "$d/.git" ] || continue
  n=$(git -C "$d" diff -U0 -- '*.java' | grep -c '^+.*Thread.sleep(5000)')
  [ "${n:-0}" -gt 0 ] && { echo "restoring $n leftover sleep(s) in $d"; git -C "$d" checkout -- '*.java'; }
done

echo "[$(ts)] 6) sweep: $RANKER top-$TOP_K on all tests (single run, no batches)"
python3 "$WRAPPER" "$CSV"
echo "[$(ts)] sweep finished (exit $?)"

echo "[$(ts)] 7) summary"
python3 - "$RES" <<'PY'
import sys, pandas as pd
try: df = pd.read_csv(sys.argv[1])
except Exception as e: print("no results yet:", e); sys.exit()
print(df.to_string(index=False))
ok = (df.iloc[:,-4:].astype(str).apply(lambda r: 'no_test_failure' not in r.values, axis=1)).sum() if len(df) else 0
print(f"\nrows: {len(df)}   reproduced (not no_test_failure): {ok}")
PY
echo "(uiSample, spring-boot: no cached ranking in the repo -> not run; ask the authors for its embeddings file)"
