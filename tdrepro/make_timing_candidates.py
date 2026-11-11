"""Build metadata/embedings/<test>_gpt2_embeddings.csv for a test that has no GPT-2 cache.
Candidates = executed methods (optionally restricted to the FlakeSync concurrent-method list), ordered by the timing prior.
Usage: python3 make_timing_candidates.py <executed_method_bodies.csv> <test_with_dot> [project-methods.txt]
Env:   MIN_LINES=4  (methods shorter than this have no line the sweep can inject into, so they are dropped)
"""
import os, sys, pandas as pd
from td_mix import timing_only_rank

bodies, test = sys.argv[1], sys.argv[2]
conc = sys.argv[3] if len(sys.argv) > 3 and os.path.exists(sys.argv[3]) else None
min_lines = int(os.environ.get("MIN_LINES", "4"))

df = pd.read_csv(bodies, dtype=str, keep_default_na=False)
print(f"executed methods: {len(df)}")
if conc:
    keys = {l.strip() for l in open(conc) if l.strip()}
    if keys:
        mk = df["Class"].str.replace(".", "/", regex=False) + "." + df["Method"] + df["Descriptor"]
        df = df[mk.isin(keys)]
        print(f"after concurrent-method filter: {len(df)}")

def n_lines(r):
    a = r.split("-")
    try: return int(a[-1]) - int(a[0]) + 1
    except Exception: return 0
df = df[df["Body"].str.strip() != ""]
df = df[df["LineRange"].map(n_lines) >= min_lines]
print(f"after dropping empty/short (<{min_lines} lines) methods: {len(df)}")
out = timing_only_rank(df)
os.makedirs("metadata/embedings", exist_ok=True)
path = f"metadata/embedings/{test}_gpt2_embeddings.csv"
out.to_csv(path, index=False)
print("wrote", path)
print(out[["Class", "Method", "LineRange", "timing_score"]].head(15).to_string())
