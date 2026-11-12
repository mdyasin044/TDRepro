#!/usr/bin/env bash
# run_uisample_timing.sh - spring-boot SampleIntegrationTests#uiSample only, candidates ranked by the TIMING PRIOR ONLY (no GPT-2).
# Run from tdrepro/ (venv active):   nohup bash run_uisample_timing.sh > run_ui.log 2>&1 &
# Env: DRY_RUN=1 (stop after printing the top-10)   TOP_K=10   MIN_LINES=4   SKIP_SETUP=1 (reuse existing traces/ file)
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
ID=621; SLUG=spring-projects/spring-boot; SHA=cf24af0bfbfdd778bfb076947ea6f012a88f372d; MODULE=spring-boot-cli
TEST='org.springframework.boot.cli.SampleIntegrationTests#uiSample'; TEST_DOT="${TEST//#/.}"
CUR="$(pwd)"; PROJ="$CUR/projects/$SLUG"; TOP_K="${TOP_K:-10}"; CSV="../data/uisample.csv"
FS_DIR="${FLAKESYNC_DIR:-}"
if [ -z "$FS_DIR" ]; then   # auto-detect the (renamed) FlakeSync folder: tdrepro/ or the repo root
  for c in ./[Ff]lake[Ss]ync* ../[Ff]lake[Ss]ync*; do [ -f "$c/pom.xml" ] && { FS_DIR="$(cd "$c" && pwd)"; break; }; done
fi
CONC_FILE="conc_executed_methods/classified/${TEST_DOT}-ResultMethods-project-methods.txt"
ts(){ date '+%F %T'; }
mkdir -p logs results traces projects
export TDREPRO_RANKER=timing TOP_K DRY_RUN="${DRY_RUN:-}"

echo "[$(ts)] java: $(java -version 2>&1 | head -1) | mvn: $(mvn -v 2>&1 | head -1)"
echo "   (this old spring-boot commit normally needs JDK 8; if the build fails with source/target errors, switch JAVA_HOME to JDK 8)"
python3 -c "import torch, transformers, pandas" 2>/dev/null || { echo "pip install torch transformers pandas"; exit 1; }
[ -f ../Results/merged_failures.csv ] || (cd ../Results && unzip -o merged_failures.zip >/dev/null)
sed -i 's/\r$//' "$CSV"

BODIES="traces/${SLUG//\//_}_${MODULE//\//_}_${TEST}_executed_method_bodies.csv"
if [ "${SKIP_SETUP:-0}" != "1" ] && [ ! -s "$BODIES" ]; then
  echo "[$(ts)] 1) clone + checkout"
  [ -d "$PROJ/.git" ] || git clone "https://github.com/$SLUG" "$PROJ" || exit 1
  git -C "$PROJ" checkout -- '*.java' 2>/dev/null
  git -C "$PROJ" checkout -q "$SHA" || exit 1
  # old poms use http:// repositories, which the servers now reject (405/403): switch every pom to https
  find "$PROJ" -name pom.xml -exec sed -i 's~http://repo.spring.io~https://repo.spring.io~g; s~http://repo.string.io~https://repo.spring.io~g; s~http://repo.gradle.org~https://repo.gradle.org~g; s~http://repo.maven.apache.org~https://repo.maven.apache.org~g; s~http://repo1.maven.org~https://repo1.maven.org~g' {} +
  find ~/.m2/repository -name "*.lastUpdated" -delete 2>/dev/null     # forget the cached "failed download" markers

  echo "[$(ts)] 2) build + JaCoCo agent in pom"
  # spring-boot-dependency-tools copies the artifact spring-boot-versions:effective-pom, which is NOT part of "-pl cli -am", so build it first
  ( cd "$PROJ" && mvn -U install -pl spring-boot-versions -am -DskipTests ) || { echo "BUILD FAILED at step 2a (spring-boot-versions)."; exit 1; }
  ( cd "$PROJ" && mvn -U install -pl "$MODULE" -am -DskipTests ) || { echo "BUILD FAILED at step 2 (dependency download). Not continuing - see the first [ERROR] above."; exit 1; }
  ( cd "$CUR/agent-pom-modify" && bash modify-project.sh "$PROJ" "jacoco" )

  echo "[$(ts)] 3) run the test once with coverage"
  ( cd "$PROJ" && mvn clean install -pl "$MODULE" -am -DskipTests ) || { echo "BUILD FAILED at step 3"; exit 1; }
  ( cd "$PROJ" && mvn test -pl "$MODULE" -Dtest="$TEST" > "$CUR/logs/${TEST}-con.txt" )
  echo "[$(ts)] 3b) FlakeSync concurrent methods (dir: ${FS_DIR:-NOT FOUND})"
  if [ -n "$FS_DIR" ]; then
    ( cd "$FS_DIR" && mvn clean install -DskipTests )
    ( cd "$PROJ" && timeout 10m mvn edu.utexas.ece:flakesync-maven-plugin:1.0-SNAPSHOT:concurrentfind -Dflakesync.testName="$TEST" -pl "$MODULE" )
    mkdir -p conc_executed_methods
    if [ -f "$PROJ/$MODULE/.flakesync/${TEST_DOT}-ResultMethods.txt" ]; then
      mv "$PROJ/$MODULE/.flakesync/${TEST_DOT}-ResultMethods.txt" conc_executed_methods/
      ( cd "$PROJ" && python3 "$CUR/classify_methods_multimodule.py" "." "$CUR/conc_executed_methods/${TEST_DOT}-ResultMethods.txt" )
    else
      echo "WARNING: FlakeSync produced no ResultMethods file; continuing with ALL executed methods"
    fi
  else
    echo "WARNING: FlakeSync folder not found (set FLAKESYNC_DIR=/path); continuing with ALL executed methods"
  fi
  [ -f "$PROJ/$MODULE/target/jacoco.exec" ] || { echo "no jacoco.exec produced - see logs/${TEST}-con.txt"; exit 1; }

  echo "[$(ts)] 4) executed methods + bodies"
  cd "$PROJ" || exit 1
  cp "$CUR/jacococli.jar" .
  CLASSFILES=""; SOURCEFILES=""
  for mod in $(find . -name target -type d -prune); do
    base=$(dirname "$mod")
    [ -d "$mod/classes" ] && CLASSFILES+="--classfiles $mod/classes "
    [ -d "$base/src/main/java" ] && SOURCEFILES+="--sourcefiles $base/src/main/java "
  done
  java -jar jacococli.jar report $MODULE/target/jacoco.exec $CLASSFILES $SOURCEFILES --xml $MODULE/target/coverage.xml
  python3 "$CUR/collect_executed_meths.py" "$MODULE" "$TEST" "$SLUG"
  readarray -t modules_array < <(find . -type d -name target -prune | sed 's|/target$||; s|^\./||' | sort -u)
  python3 "$CUR/collect_method_body.py" "$MODULE" "$TEST" "$SLUG" "${modules_array[@]}"
  mv ./*_"${TEST}"_executed_method*.csv "$CUR/traces/" 2>/dev/null
  git checkout -- '*.java' 2>/dev/null          # never 'git checkout -- .' (keeps the pom edit)
  cd "$CUR" || exit 1
fi
[ -s "$BODIES" ] || { echo "missing $BODIES - the executed-method step failed"; exit 1; }

echo "[$(ts)] 5) candidate list ordered by timing prior (no GPT-2)"
python3 make_timing_candidates.py "$BODIES" "$TEST_DOT" "$CONC_FILE" || exit 1

echo "[$(ts)] 6) baseline failure log"
OUT="logs/${ID}_${MODULE//\//.}_${TEST}_stacktrace.csv"
[ -s "$OUT" ] || python3 find_failure_message_and_save.py "$ID" "$SLUG" "$SHA" "$MODULE" "$TEST" ../Results/merged_failures.csv "${MODULE//\//.}" "spring-boot" 2>&1 | grep -E "No matching|Error|Traceback"
[ -s "$OUT" ] || { echo "baseline log missing: $OUT"; exit 1; }

echo "[$(ts)] 7) sweep (timing prior only, top-$TOP_K)"
python3 wrapper_for_sequential_delay_injection_tdmix.py "$CSV"
echo "[$(ts)] done. result: results/sequential_delay_injection_timing_top${TOP_K}.csv"
cat "results/sequential_delay_injection_timing_top${TOP_K}.csv" 2>/dev/null
