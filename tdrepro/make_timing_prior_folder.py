"""
Build timing-prior/ : for every test in the input CSV, ALL candidate methods ranked by the timing prior only
(no GPT-2 similarity is used for the order), with their scores, plus a top-10 summary.

Usage (from tdrepro/):  python3 make_timing_prior_folder.py ../data/failed_14_tests.csv [embedings_dir] [out_dir]
The cached  embedings/<test>_gpt2_embeddings.csv  is used ONLY as the list of candidate methods (executed + concurrent
methods with their Body); its similarity columns are ignored.

Order = timing_score (descending), then Lines Covered (descending: more executed lines = more places to inject),
then Class, Method, Descriptor (alphabetical), so ties never depend on GPT-2.
timing_score = timing_api_calls (call sites of FlakeRake's timing APIs, own signature excluded) + synchronized_count.
"will_run" = position among the methods the sweep will really try: a method already tried under the same
Class.Method (an overload) is skipped, as in the sweep itself.
"""
import csv, os, re, sys
import pandas as pd
from td_mix import _api_names, _KW

tests_csv = sys.argv[1]
emb_dir = sys.argv[2] if len(sys.argv) > 2 else "embedings"
out_dir = sys.argv[3] if len(sys.argv) > 3 else "timing-prior"
TOP_K = int(os.environ.get("TOP_K", "10"))
os.makedirs(os.path.join(out_dir, "ranked"), exist_ok=True)
names = _api_names()

def parts(body):
    body = body if isinstance(body, str) else ""
    inner = body.split("{", 1)[1] if "{" in body else body
    calls = [m for m in re.findall(r"\b([A-Za-z_]\w*)\s*\(", inner) if m not in _KW]
    return sum(1 for c in calls if c in names), len(re.findall(r"\bsynchronized\b", body))

summary, report = [], []
for t in csv.DictReader(open(tests_csv)):
    test = t["test"].strip(); tdot = test.replace("#", ".")
    src = os.path.join(emb_dir, f"{tdot}_gpt2_embeddings.csv")
    if not os.path.exists(src):
        report.append((t["id"], test, "NO CACHED CANDIDATE LIST - cannot rank (needs the pipeline)")); continue
    df = pd.read_csv(src, dtype=str, keep_default_na=False)
    pr = df["Body"].map(parts)
    df.insert(0, "timing_score", [a + b for a, b in pr])
    df.insert(1, "timing_api_calls", [a for a, _ in pr])
    df.insert(2, "synchronized_count", [b for _, b in pr])
    df["_cov"] = pd.to_numeric(df["Lines Covered"], errors="coerce").fillna(0)
    df = df.sort_values(["timing_score", "_cov", "Class", "Method", "Descriptor"],
                        ascending=[False, False, True, True, True], kind="stable").drop(columns="_cov").reset_index(drop=True)
    seen, will, dup = set(), [], []
    for _, r in df.iterrows():
        k = (r["Class"], r["Method"])
        if k in seen: will.append(""); dup.append("overload of an earlier row - skipped")
        else:
            seen.add(k); n = len([w for w in will if w != ""]) + 1
            will.append(n if n <= TOP_K else ""); dup.append("")
    df.insert(0, "rank", range(1, len(df) + 1)); df.insert(1, "will_run", will); df.insert(2, "note", dup)
    df.to_csv(os.path.join(out_dir, "ranked", f"{tdot}.csv"), index=False, lineterminator="\n")
    top = df[df["will_run"] != ""]
    for _, r in top.iterrows():
        a, b = (r["LineRange"].split("-") + ["", ""])[:2]
        n_lines = (int(b) - int(a) - 2) if a.isdigit() and b.isdigit() else ""
        summary.append(dict(test_id=t["id"], test=test.split("#")[-1], will_run=r["will_run"], rank_in_file=r["rank"], timing_score=r["timing_score"],
                            timing_api_calls=r["timing_api_calls"], synchronized_count=r["synchronized_count"],
                            Class=r["Class"], Method=r["Method"], LineRange=r["LineRange"], lines_to_try_approx=n_lines))
    pos = int((df["timing_score"] > 0).sum())
    report.append((t["id"], test, f"{len(df)} candidates, {pos} with timing_score>0, top-{TOP_K} scores: {list(top['timing_score'])}"))
pd.DataFrame(summary).to_csv(os.path.join(out_dir, "top10_summary.csv"), index=False, lineterminator="\n")
for r in report: print(*r, sep=" | ")
