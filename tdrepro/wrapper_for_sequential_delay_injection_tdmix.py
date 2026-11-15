# TD-Mix version of the sequential (all-lines) delay-injection baseline.
#   python3 wrapper_for_sequential_delay_injection_tdmix.py ../data/failed_tests_input.csv
# Environment variables:
#   TDREPRO_RANKER=td-mix|gpt2|timing|timing-prior   ranker applied to the cached GPT-2 list (default td-mix)
#   TOP_K=10                     how many ranked methods to try per test (default 10)
#   DRY_RUN=1                    only print the methods that would be tried, run nothing
# Results go to results/sequential_delay_injection_<ranker>_top<K>.csv (the authors' file is never touched).
# Tests already present in that results file are skipped, so the script can be restarted.

import csv
import os
import subprocess
from pathlib import Path
from typing import Optional
from modify_java_file import inject_sleep_before_line
import re
import time
import sys
import pandas as pd
from td_mix import td_mix_rank, timing_only_rank
#from generating_reproducing_config import log_similarity_check
from utils import find_class_file, log_similarity_check


CURRENT_DIR = os.getcwd()
#def has_errors_or_failures(path):
#    with open(path, 'r') as f:
#        text = f.read()
#    return 'Errors: 1' in text or 'Failures: 1' in text

output_fields = ["slug", "module", "test", "method_id", "line_number", "actual_line", "log_file", "class_name", "method_name", "total_time_seconds", "iteration_count", "failure_detected"]
def save_result(output_csv, slug, module, test, row_id, line_number, actual_line, log_file, class_name, method_name, total_time_seconds, iteration_count, failure_detected):
    with open(output_csv, "a", newline='') as outf:
        writer = csv.DictWriter(outf, fieldnames=output_fields)
        if outf.tell() == 0:
            writer.writeheader()
        writer.writerow({
            "slug": slug,
            "module": module,
            "test": test,
            "method_id": row_id,
            "line_number": line_number,
            "actual_line": actual_line,
            "log_file": log_file,
            "class_name": class_name,
            "method_name": method_name,
            "total_time_seconds": round(total_time_seconds, 2),
            "iteration_count": iteration_count,
            "failure_detected": failure_detected
        })
def has_errors_or_failures(path):
    with open(path, 'r') as f:
        text = f.read()
    # Match 'Errors: N' or 'Failures: N' where N > 0
    errors = re.search(r'Errors:\s*[1-9][0-9]*', text)
    failures = re.search(r'Failures:\s*[1-9][0-9]*', text)
    return errors is not None or failures is not None

def find_source_file_with_find(repos_root: str, slug: str, class_path: str) -> Optional[Path]:
    """
    Search under repos_root/slug for a file matching class_path.
    First try exact package tail (*/org/foo/Bar.java), then fall back to filename only.
    """
    base = Path(repos_root) / Path(*slug.split("/"))

    # 1) Prefer exact package path match
    cmd = ["find", str(base), "-type", "f", "-path", f"*/{class_path}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    hits = [Path(p) for p in res.stdout.splitlines() if p.strip()]
    if hits:
        if len(hits) > 1:
            print(f"⚠️ Multiple matches for {class_path}; using first:\n" + "\n".join(str(h) for h in hits))
        return hits[0]

    # 2) Fallback: filename only (case-insensitive)
    fname = Path(class_path).name
    cmd = ["find", str(base), "-type", "f", "-iname", fname]
    res = subprocess.run(cmd, capture_output=True, text=True)
    hits = [Path(p) for p in res.stdout.splitlines() if p.strip()]
    if hits:
        if len(hits) > 1:
            print(f"Multiple files named {fname}; consider using package path to disambiguate.")
        return hits[0]

    print(f"No file found for {class_path} under {base}")
    return None

# Configuration: path to the CSV data file
#csv_file = 'metadata/embedings/org.java_websocket.issues.Issue580Test.runNoCloseBlockingTestScenario2_bigbird_embeddings.csv'  # Update this to the actual CSV filename/path
# Variables for the test run command (ensure these are set to real values as needed)
#slug = "your-project-slug"        # e.g., "org.java-websocket/Java-WebSocket"
#module = "your-module-path"       # e.g., "" (if root project) or "module-name"
#test  = "your.test.ClassName#testMethod"  # e.g., a specific test to run
#retry_count = 0
run_id = 0

input_csv=sys.argv[1] #"../data/all_82_tests.csv"
#input_csv="../data/l.csv"
#input_csv="l"
model_name = "gpt2" #"llama" #"tf-idf"#"gpt2"
#cosine_weight = sys.argv[2] #70_30
RANKER = os.environ.get("TDREPRO_RANKER", "td-mix").lower()
TOP_K = int(os.environ.get("TOP_K", "10"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
os.makedirs("results", exist_ok=True)
output_csv = f"results/sequential_delay_injection_{RANKER.replace('-', '')}_top{TOP_K}.csv"

def load_ranked_methods(csv_file):
    """Authors' cached GPT-2 list (best first); re-ordered by TD-Mix when RANKER == 'td-mix'. Returns list of dict rows."""
    df = pd.read_csv(csv_file, dtype=str, keep_default_na=False)
    if RANKER == "timing-prior":      # file already sorted by the timing prior (timing-prior/ranked/*.csv): use as is
        return df.to_dict("records")
    if RANKER == "td-mix":
        df = td_mix_rank(df)
    elif RANKER == "timing":          # timing prior only, no GPT-2 list
        df = timing_only_rank(df)
    return df.to_dict("records")

def already_done(path):
    done = set()
    if os.path.exists(path):
        with open(path, newline='') as f:
            for r in csv.DictReader(f):
                done.add(r["test"].replace("#", "."))
    return done
DONE = already_done(output_csv)

def safe_log_similarity(failure_log_csv, test_run_log, test):
    """log_similarity_check, but a crash inside it no longer kills the whole (hours-long) run: it is reported and counted as 'no match'."""
    try:
        return log_similarity_check(failure_log_csv, test_run_log, test)
    except Exception as e:
        print("[ERROR] log_similarity_check crashed, treating as NOT matched:", repr(e))
        return False

import time as _time
_T0 = _time.time()
def show_status(test, method_count, class_name, method_name, idx, n_lines, iteration_count):
    el = int(_time.time() - _T0)
    msg = (f"[STATUS] {test.split('.')[-1]} | method {method_count}/{TOP_K} {class_name.split('.')[-1]}.{method_name}"
           f" | line {idx+1}/{n_lines} | lines tried so far: {iteration_count} | elapsed {el//3600}h{(el%3600)//60:02d}m")
    print("\n" + "="*100 + "\n" + msg + "\n" + "="*100, flush=True)
    try:
        with open("status.txt", "w") as sf: sf.write(msg + "\n")
    except Exception: pass

def run_once(run_id, class_path_list, line_number, method_name, descriptor, code_line, slug, module, test, retry_count, idx, tmp=2):
    print("***code_line=", code_line)
    inject_sleep_before_line(class_path_list, line_number, method_name, descriptor, code_line)
    tag = f"{retry_count}_{idx}_{run_id}"
    before, after = test.rsplit('.', 1)
    test_with_hash = f"{before}#{after}"
    try:
        print("./run_test.sh", slug, module, test, tag, "logs-to-reproduce-sequential-delay-injection")
        if tmp == 100:
            exit()
        result_run = subprocess.run(
            ["./run_test.sh", slug, module, test, tag, "logs-to-reproduce-sequential-delay-injection"],
            check=True, text=True, capture_output=True
        )
        out = result_run.stdout.strip()
        firstLine = out.splitlines()[0]  # "Failure not found." or "Failure found."
        failed = (firstLine == "Failure found.")
        
        return failed, CURRENT_DIR +"/logs-to-reproduce-sequential-delay-injection/" + test_with_hash + "-con-after-changedCode-"+tag+".txt"
        #return 1, CURRENT_DIR +"/logs-to-reproduce-sequential-delay-injection/" + test_with_hash + "-con-after-changedCode-"+tag+".txt"
    except subprocess.CalledProcessError as e:
        print("run_test.sh failed with exit code", e.returncode)
        print("--- stdout ---"); print(e.stdout)
        print("--- stderr ---"); print(e.stderr)

        # Inspect produced log to decide if it was a failure
        currentDir_when_exception_occurs = os.getcwd()
        log_file = (currentDir_when_exception_occurs + "/logs-to-reproduce-sequential-delay-injection/" +
                    f"{test_with_hash}-con-after-changedCode-{tag}.txt")
        print("log file name=", log_file)
        log_text = None
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                log_text = f.read()
        if has_errors_or_failures(log_file):
            print("Found Errors: 1 or Failures: 1")
            # Will check if the failure is the one that we desired
            return True, log_file
        else:
            print("No Errors: 1 or Failures: 1")
            return False, log_file

with open(input_csv, newline='') as inf:
    reader = csv.DictReader(inf)
    for test_info in reader: #For each test
        print("**** test_info=", test_info)
        failure_count = 0
        iteration_count = 0
        start_time = time.time()
        id = test_info['id']
        if id.startswith('#'):
            continue
        if test_info['test'].replace('#', '.') in DONE:
            print("already in results, skipping:", test_info['test'])
            continue
        slug = test_info['slug']
        commit = test_info['commit']
        module = test_info['module']
        module_with_dot = module.replace("/", ".")
        test = test_info['test']
        if module == ".":
            proj_name_only = slug.split('/')[1]
            failure_log_csv = f"logs/{id}_{proj_name_only}_{test}_stacktrace.csv"
        else:
            failure_log_csv = f"logs/{id}_{module_with_dot}_{test}_stacktrace.csv"

        print(failure_log_csv)
        if not os.path.exists(failure_log_csv):
            print(f"[SKIP] baseline failure log not found: {failure_log_csv}  (create it with find_failure_message_and_save.py)")
            continue
        method_count = 0
        seen_methods = set()
        test_with_dot = test.replace("#", ".")
        csv_file = "metadata/embedings/" + test_with_dot +"_"+model_name+ "_embeddings.csv"
        if RANKER == "timing-prior":
            csv_file = "timing-prior/ranked/" + test_with_dot + ".csv"
        failure_detected = 0 
        # Open and read the CSV data
        with open(csv_file, newline='') as f:  #ranked_method_list (kept only to preserve the block structure)
            reader = load_ranked_methods(csv_file)
            if DRY_RUN:
                print(f"[DRY_RUN] {test} ranker={RANKER} top-{TOP_K}:")
                _seen = set()
                for k, m_ in enumerate(reader[:TOP_K], 1):
                    key_ = (m_['Class'], m_['Method'], m_['LineRange'])
                    dup_ = "   [DUPLICATE - will be skipped]" if key_ in _seen else ""
                    _seen.add(key_)
                    print(f"   {k}. {m_['Class']}.{m_['Method']}  lines {m_['LineRange']}{dup_}")
                continue
            #for row in reader:
            for ranked_meth_id, ranked_meth in enumerate(reader):
                if method_count >= TOP_K:
                    break
                method_count +=1
                _key = (ranked_meth['Class'], ranked_meth['Method'], ranked_meth['LineRange'])
                if _key in seen_methods:
                    print(f"[DEDUP] method {method_count}/{TOP_K} {_key} already tried (overload row with the same lines); skipping", flush=True)
                    continue
                seen_methods.add(_key)
                class_name = ranked_meth['Class']
                #if '$' in class_name:
                #    class_name = class_name.split('$', 1)[0]
                method_name = ranked_meth['Method']
                descriptor = ranked_meth['Descriptor']
                line_range = ranked_meth['LineRange']
                # Parse line range (e.g., "555-570")
                if '-' in line_range:
                    start_line, end_line = map(int, line_range.split('-'))
                else:
                    start_line = end_line = int(line_range)
                # Construct file path from class name (package to path)
                #Java file not found for tachyon/thrift/WorkerService$Client.java
                class_path_list = find_class_file(class_name, slug, module) 
                '''class_path = class_name.replace('.', os.sep) + ".java"
                java_file_path = find_source_file_with_find("projects", slug, class_path)
                print("Resolved:", java_file_path)
                if java_file_path is None or not os.path.exists(java_file_path):
                    print(f"Java file not found for {class_path}, skipping...", flush=True)
                    print("Failure_count=", failure_count)
                    continue
                
                print("slug, commit, module, test, class_name, method_name, descriptor, line_range=",slug, commit, module, test, class_name, method_name, descriptor, line_range, start_line, end_line, class_path)
                #"projects"+slug+module+
                candidates = [str(java_file_path)]   # list of one or more paths'''
                print(type(class_path_list))
                if not class_path_list:
                    print(f"No file found for {class_name}")
                else:
                    java_file_path = class_path_list[0]
                    print("java_file_path=", java_file_path)
                    # Read original file content to restore later

                    with open(java_file_path, 'r') as source_file:
                        original_lines = source_file.readlines()

                        # Iterate through each line inside the method body (exclude signature and closing brace)
                        #for line_no in range(start_line + 1, end_line):
                        for idx, line_no in enumerate(range(start_line + 2, end_line)):
                            print("line_no=", line_no)
                            failure_count = 0
                            iteration_count += 1
                            show_status(test, method_count, class_name, method_name, idx, max(end_line - (start_line + 2), 1), iteration_count)
                            code_line = original_lines[line_no - 1]
                            print("**** code_line=", code_line)
                            currentDir_when_exception_occurs = os.getcwd()
                            os.makedirs(currentDir_when_exception_occurs+"/logs-to-reproduce-sequential-delay-injection/", exist_ok=True)
                            failure_happened_but_log_matched = True
                            print("0, candidates, line_no, method_name, descriptor, code_line, slug, module, test_with_dot, retry_count, idx=", 0, class_path_list, line_no, method_name, descriptor, code_line, slug, module, test_with_dot, method_count, idx)
                            first_failed, test_run_log = run_once(0, class_path_list, line_no, method_name, descriptor, code_line, slug, module, test_with_dot, method_count, idx)
                            #exit()

                            if not first_failed:
                                print("First run: no failure; skipping additional runs.")
                                print("Only 0/1 runs failed. Not considering as valid failure.")
                            else:
                               # First run failed → run 4 more times (total 5)
                                #break
                                #exit()
                                print("HA HA TEST FAILED *************")
                                failure_count = 1
                                failure_detected = 1
                                log_similar = safe_log_similarity(failure_log_csv, test_run_log, test)
                                if not log_similar:
                                    print("failure log does not match.")
                                    failure_happened_but_log_matched = False
                                else: 

                                    print('********** FIRST FAILURE FOUND ****************, and log_matched', test_run_log)
                                    for run_id in range(1, 5):

                                        print('********** Running REPEATED FAILURE TO GET ****************')
                                        #if run_once(run_id, candidates, line_no, method_name, descriptor, code_line, slug, module, test_with_dot, retry_count, idx):
                                        #    failure_count += 1
                                        print("****run_id, candidates, line_no, method_name, descriptor, code_line, slug, module, test_with_dot, retry_count, idx=", run_id, class_path_list, line_no, method_name, descriptor, code_line,     slug, module, test_with_dot, method_count, idx)
                                        _failed, test_run_log = run_once(run_id, class_path_list, line_no, method_name, descriptor, code_line, slug, module, test_with_dot, method_count, idx)
                                        log_similar = safe_log_similarity(failure_log_csv, test_run_log, test)
                                        if log_similar and _failed: #run_once(run_id, class_path_list, line_number, method_name, descriptor, code_line, slug, module, test, retry_count, idx):
                                            #Call the function to check the log match 
                                            failure_count += 1

                                            print('********** Repeated FAILURE FOUND ****************')

                                            #exit()
                                        else:
                                            print("*** Repeated failure log does not match.")
                                            failure_happened_but_log_matched = False
                                        #Call to the GPT that log does not match, so find a different location
                                    if failure_count >= 3:
                                        before, after = test_with_dot.rsplit('.', 1)
                                        test_with_hash = f"{before}#{after}"
                                        print(f"Failure found in {failure_count}/5 runs.")
                                        # currentDir_when_exception_occurs = os.getcwd()
                                        log_file = currentDir_when_exception_occurs+"/logs-to-reproduce-sequential-delay-injection/"+test_with_hash+"-con-after-changedCode-"+str(method_count) +"_" +str(idx)+ "_" + str(run_id)+".txt"
                                        total_time_seconds = time.time() - start_time
                                        save_result(output_csv, slug, module, test_with_dot, ranked_meth_id, line_no, code_line, log_file, class_name, method_name, total_time_seconds, iteration_count, failure_detected)
                                        break
                                        #return line, f"{retry_count}_{idx}", "Failure found."
                                    else:
                                        print(f"Only {failure_count}/5 runs failed. Not considering as valid failure.")
                    if failure_count >=3:
                        break
        if failure_count < 3: #not reproduced (also when the last line had a first-run failure with a non-matching log)
            total_time_seconds = time.time() - start_time
            save_result(output_csv, slug, module, test_with_dot, "no_test_failure", "NA", "NA", "NA", "NA", "NA", total_time_seconds, iteration_count, 0)
            print("I AM HERE", output_csv, slug, module, test, "no_test_failure", "NA", "NA", "NA", "NA", "NA", total_time_seconds, iteration_count, failure_detected)

        #exit()
        
