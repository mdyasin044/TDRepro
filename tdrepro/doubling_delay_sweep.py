"""
Doubling-delay sweep on the timing-prior top-10 methods (no GPT-2), every line, for the "failure found but log mismatch" tests.

For each test (in CSV order), for each of the top-K methods of  <PRIOR_DIR>/ranked/<test>.csv  (timing prior order), for each line
(start+2 .. end-1, same lines as the main sweep):
    delay = START_MS (100)
    loop:
        inject  Thread.sleep(delay)  before the line, run the test once
        test PASSES                                   -> delay = delay * 2   (last step is clamped to MAX_MS = 25000), go on
        test FAILS, log does NOT match baseline       -> MISMATCH=stop (default): give up this line | MISMATCH=continue: double and go on
        test FAILS, log MATCHES baseline              -> run until RUNS (5) runs are done
                                                           >= 3 of 5 match  -> VALID: line reproduced (test is finished when STOP_ON_SUCCESS=1, default)
                                                           <  3 of 5        -> keep doubling
        build error / no test result (timeout)        -> give up this line
    stop when delay > MAX_MS has been tried.
Delay sequence with the defaults: 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25000 ms.

Usage (from tdrepro/, venv active):
    DRY_RUN=1 python3 doubling_delay_sweep.py ../data/log_mismatch_5_tests.csv      # lists methods/lines, runs nothing
    nohup python3 doubling_delay_sweep.py ../data/log_mismatch_5_tests.csv > run_doubling.log 2>&1 &
Env: PRIOR_DIR=timing-prior-mismatch TOP_K=10 START_MS=100 MAX_MS=25000 RUNS=5 NEED=3 MISMATCH=stop|continue STOP_ON_SUCCESS=1 ONLY=56,621 DRY_RUN=1
Results: results/doubling_delay_sweep.csv (one row per line x delay), results/doubling_runs.csv (one row per run), results/doubling_summary.csv
Restartable: finished (line, delay) rows are read back and skipped.
"""
import csv, os, re, subprocess, sys, time
import pandas as pd

CUR = os.getcwd()
PRIOR_DIR = os.environ.get("PRIOR_DIR", "timing-prior-mismatch")
TOP_K = int(os.environ.get("TOP_K", "10"))
START_MS = int(os.environ.get("START_MS", "100"))
MAX_MS = int(os.environ.get("MAX_MS", "25000"))
RUNS = int(os.environ.get("RUNS", "5"))
NEED = int(os.environ.get("NEED", "3"))
MISMATCH = os.environ.get("MISMATCH", "stop").lower()
STOP_ON_SUCCESS = os.environ.get("STOP_ON_SUCCESS", "1") == "1"
ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
LOGDIR = "logs-doubling"
SWEEP, RUNS_CSV, SUMMARY = "results/doubling_delay_sweep.csv", "results/doubling_runs.csv", "results/doubling_summary.csv"
SWEEP_H = ["id", "test", "method_rank", "class", "method", "java_file", "line_no", "delay_ms", "status", "runs", "failed_runs", "matched_runs", "minutes"]
RUNS_H = ["id", "java_file", "line_no", "delay_ms", "run", "failed", "log_matched", "run_log"]

def delay_list():
    d, out = START_MS, []
    while d < MAX_MS:
        out.append(d); d *= 2
    out.append(MAX_MS)
    return out
DELAYS = delay_list()

def append(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new: w.writerow(header)
        w.writerow(row)

def baseline_log(t):
    if t["module"] == ".": return f"logs/{t['id']}_{t['slug'].split('/')[1]}_{t['test']}_stacktrace.csv"
    return f"logs/{t['id']}_{t['module'].replace('/', '.')}_{t['test']}_stacktrace.csv"

def methods_of(t):
    """top-K methods in timing-prior order, as rows of <PRIOR_DIR>/ranked/<test>.csv with will_run set"""
    f = f"{PRIOR_DIR}/ranked/{t['test'].replace('#', '.')}.csv"
    df = pd.read_csv(f, dtype=str, keep_default_na=False)
    df = df[df["will_run"].str.strip() != ""]
    df = df.assign(_k=pd.to_numeric(df["will_run"])).sort_values("_k")
    return [r for r in df.to_dict("records") if r["_k"] <= TOP_K]

def line_range(m):
    a = m["LineRange"].split("-")
    s, e = int(a[0]), int(a[-1])
    return s, e, list(range(s + 2, e))          # same lines as the main sweep

if DRY_RUN:
    pass
else:
    from modify_java_file import find_statement_start_line
    from utils import find_class_file, log_similarity_check

def inject(java_file, line_no, ms):
    with open(java_file) as f: lines = f.readlines()
    if len(lines) < line_no: return False
    at = find_statement_start_line(lines, line_no)
    indent = re.match(r"^(\s*)", lines[at - 1]).group(1)
    block = [f"{indent}try {{\n", f"{indent}    Thread.sleep({ms});\n", f"{indent}}} catch (InterruptedException e) {{\n",
             f"{indent}    Thread.currentThread().interrupt();\n", f"{indent}}}\n"]
    for i, b in enumerate(block): lines.insert(at - 1 + i, b)
    with open(java_file, "w") as f: f.writelines(lines)
    return True

def restore(slug):
    subprocess.run(["git", "-C", f"projects/{slug}", "checkout", "--", "*.java"], capture_output=True)   # never 'checkout -- .'

def one_run(t, java_file, line_no, ms, run_i):
    """-> (status, run_log)   status in fail | pass | error   (error = build error / no test result / bad line)"""
    tag = f"D{t['id']}_{ms}_{os.path.basename(java_file)[:-5]}{line_no}_r{run_i}"
    if not inject(java_file, line_no, ms):
        return "error", ""
    res = subprocess.run(["./run_test.sh", t["slug"], t["module"], t["test"].replace("#", "."), tag, LOGDIR], capture_output=True, text=True)
    run_log = f"{CUR}/{LOGDIR}/{t['test']}-con-after-changedCode-{tag}.txt"
    out = (res.stdout or "").strip().splitlines()
    if out and out[0] == "Failure found.": return "fail", run_log
    try: txt = open(run_log, errors="ignore").read()
    except Exception: txt = ""
    if "Tests run:" in txt and "COMPILATION ERROR" not in txt: return "pass", run_log      # test really ran and did not fail
    return "error", run_log

def matches(t, run_log):
    try: return bool(log_similarity_check(baseline_log(t), run_log, t["test"]))
    except Exception as e:
        print("[ERROR] log_similarity_check crashed -> counted as NO match:", repr(e), flush=True); return False

def load_done():
    d = {}
    if os.path.exists(SWEEP):
        for r in csv.DictReader(open(SWEEP)):
            d[(r["id"], r["java_file"], r["line_no"], r["delay_ms"])] = r
    return d

def summarize():
    if not os.path.exists(SWEEP): return
    df = pd.read_csv(SWEEP, dtype={"id": str})
    rows = []
    for tid, g in df.groupby("id", sort=False):
        lines = g.groupby(["java_file", "line_no"])
        failed = g[g["failed_runs"] > 0].groupby(["java_file", "line_no"]).ngroups
        matched = g[g["matched_runs"] > 0].groupby(["java_file", "line_no"]).ngroups
        v = g[g["status"] == "VALID"]
        rows.append(dict(id=tid, test=g["test"].iloc[0].split("#")[-1], lines_tried=lines.ngroups, lines_with_failure=failed,
                         lines_matched=matched, best_match=f"{int(g['matched_runs'].max())}/{RUNS}", valid_lines=len(v),
                         valid_at=(f"{os.path.basename(v.iloc[0]['java_file'])}:{v.iloc[0]['line_no']} @ {v.iloc[0]['delay_ms']} ms" if len(v) else ""),
                         hours=round(float(g["minutes"].sum()) / 60, 2)))
    pd.DataFrame(rows).to_csv(SUMMARY, index=False, lineterminator="\n")
    print(pd.DataFrame(rows).to_string(index=False), flush=True)

def main():
    path = [a for a in sys.argv[1:] if not a.startswith("--")][0]
    tests = [t for t in csv.DictReader(open(path)) if not t["id"].startswith("#") and (not ONLY or t["id"] in ONLY)]
    os.makedirs("results", exist_ok=True); os.makedirs(LOGDIR, exist_ok=True)
    print(f"delays (ms): {DELAYS} | top-{TOP_K} methods | runs on a matching failure: {RUNS} (valid if >= {NEED}) | on mismatch: {MISMATCH}", flush=True)
    if DRY_RUN:
        for t in tests:
            ms = methods_of(t); n = sum(len(line_range(m)[2]) for m in ms)
            print(f"\n[DRY_RUN] id={t['id']} {t['test'].split('#')[-1]}: {len(ms)} methods, {n} lines, up to {n*len(DELAYS)} runs (baseline log {'OK' if os.path.exists(baseline_log(t)) else 'MISSING: ' + baseline_log(t)})")
            for m in ms: print(f"   {m['_k']:>2}. {m['Class'].split('.')[-1]}.{m['Method']}  lines {m['LineRange']}  ({len(line_range(m)[2])} lines)  timing_score={m['timing_score']}")
        return
    done = load_done(); T0 = time.time()
    for t in tests:
        tid = t["id"]
        if not os.path.exists(baseline_log(t)):
            print(f"[SKIP] id={tid}: baseline log missing: {baseline_log(t)}", flush=True); continue
        if any(k[0] == tid and r["status"] == "VALID" for k, r in done.items()) and STOP_ON_SUCCESS:
            print(f"[SKIP] id={tid}: already has a VALID line", flush=True); continue
        restore(t["slug"])
        methods = methods_of(t); n_total = sum(len(line_range(m)[2]) for m in methods); n_done = 0; test_valid = False
        print(f"\n##### id={tid} {t['test']}: {len(methods)} methods, {n_total} lines", flush=True)
        for m in methods:
            if test_valid and STOP_ON_SUCCESS: break
            cls = m["Class"]
            cand = find_class_file(cls, t["slug"], t["module"])
            if not cand: print(f"[SKIP] no source file for {cls}", flush=True); n_done += len(line_range(m)[2]); continue
            java_file = cand[0]
            for line_no in line_range(m)[2]:
                n_done += 1
                if test_valid and STOP_ON_SUCCESS: break
                for ms in DELAYS:
                    key = (tid, java_file, str(line_no), str(ms))
                    if key in done:                                   # resume: replay the decision of the finished step
                        st = done[key]["status"]
                        if st == "VALID": test_valid = True; break
                        if st in ("error",) or (st == "fail_mismatch" and MISMATCH == "stop"): break
                        continue
                    el = int(time.time() - T0)
                    msg = (f"[STATUS] {tid} {t['test'].split('#')[-1]} | {m['_k']}/{TOP_K} {cls.split('.')[-1]}.{m['Method']} | line {line_no} "
                           f"({n_done}/{n_total}) | delay {ms} ms | elapsed {el//3600}h{el//60%60:02d}m")
                    print("=" * 100 + "\n" + msg + "\n" + "=" * 100, flush=True)
                    open("status.txt", "w").write(msg + "\n")
                    started = time.time(); runs = failed_runs = matched = 0; status = "pass"
                    try:
                        st, log = one_run(t, java_file, line_no, ms, 1); runs = 1
                        if st == "error": status = "error"
                        elif st == "pass": status = "pass"
                        else:
                            failed_runs = 1; mt = matches(t, log); matched += mt
                            append(RUNS_CSV, RUNS_H, [tid, java_file, line_no, ms, 1, 1, int(mt), log])
                            if not mt: status = "fail_mismatch"
                            else:
                                for r in range(2, RUNS + 1):
                                    st2, log2 = one_run(t, java_file, line_no, ms, r); runs += 1
                                    f2 = st2 == "fail"; m2 = f2 and matches(t, log2)
                                    failed_runs += f2; matched += m2
                                    append(RUNS_CSV, RUNS_H, [tid, java_file, line_no, ms, r, int(f2), int(m2), log2])
                                status = "VALID" if matched >= NEED else "match_lt3"
                        if status in ("pass", "error"):
                            append(RUNS_CSV, RUNS_H, [tid, java_file, line_no, ms, 1, 0, 0, log])
                    finally:
                        restore(t["slug"])
                    row = [tid, t["test"], m["_k"], cls, m["Method"], java_file, line_no, ms, status, runs, failed_runs, matched, f"{(time.time()-started)/60:.1f}"]
                    append(SWEEP, SWEEP_H, row); done[key] = dict(zip(SWEEP_H, map(str, row)))
                    print(f"[RESULT] id={tid} {os.path.basename(java_file)}:{line_no} {ms} ms -> {status} (failed {failed_runs}/{runs}, matched {matched}/{runs})", flush=True)
                    if status == "VALID":
                        test_valid = True; break
                    if status == "error" or (status == "fail_mismatch" and MISMATCH == "stop"): break
        print(f"##### id={tid} finished (valid={test_valid})", flush=True)
    print("\nDONE", flush=True)
    summarize()

main()
