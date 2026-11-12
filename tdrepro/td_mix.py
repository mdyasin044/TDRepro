"""
TD-Mix method ranker for TDRepro.

Input : a DataFrame already sorted by GPT-2 similarity (rank_methods_by_llm_embedding_similarity output,
        or the cached metadata/embedings/<test>_gpt2_embeddings.csv). Needs a 'Body' column.
Output: same rows (nothing dropped), re-ordered so that `.head(top_k_method)` takes
        alternately one method from the GPT-2 list and one from the timing-prior list.

Timing prior = number of call sites whose callee name is one of FlakeRake's timing-related JDK APIs
               (timing_api_names.txt) + number of `synchronized` occurrences. Ties keep GPT-2 order, so when no
               method has any timing signal the result is exactly the original GPT-2 order.
"""
import os
import re
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_KW = {'if', 'for', 'while', 'switch', 'catch', 'synchronized', 'return', 'new', 'super', 'this', 'assert', 'throw'}
_API_NAMES = None


def _api_names():
    global _API_NAMES
    if _API_NAMES is None:
        with open(os.path.join(_HERE, "timing_api_names.txt"), encoding="utf-8") as f:
            _API_NAMES = {l.strip() for l in f if l.strip() and not l.startswith("#")}
    return _API_NAMES


def timing_score(body) -> int:
    body = "" if not isinstance(body, str) else body
    inner = body.split("{", 1)[1] if "{" in body else body          # drop the method's own signature
    names = _api_names()
    calls = [m for m in re.findall(r"\b([A-Za-z_]\w*)\s*\(", inner) if m not in _KW]
    return sum(1 for c in calls if c in names) + len(re.findall(r"\bsynchronized\b", body))


def td_mix_rank(ranked_df: pd.DataFrame) -> pd.DataFrame:
    """ranked_df must be in GPT-2 order (best first). Returns the full df in TD-Mix order."""
    df = ranked_df.reset_index(drop=True)
    n = len(df)
    gpt2_order = list(range(n))
    scores = [timing_score(b) for b in df["Body"]]
    timing_order = sorted(range(n), key=lambda i: (-scores[i], i))   # stable: ties -> GPT-2 order
    seen, mixed = set(), []
    for a, b in zip(gpt2_order, timing_order):
        for i in (a, b):
            if i not in seen:
                seen.add(i)
                mixed.append(i)
    out = df.iloc[mixed].reset_index(drop=True)
    out["timing_score"] = [scores[i] for i in mixed]
    return out


def timing_only_rank(df: pd.DataFrame) -> pd.DataFrame:
    """No GPT-2 list: order purely by the timing prior (highest first, ties keep the input order)."""
    df = df.reset_index(drop=True)
    scores = [timing_score(b) for b in df["Body"]]
    order = sorted(range(len(df)), key=lambda i: (-scores[i], i))
    out = df.iloc[order].reset_index(drop=True)
    out["timing_score"] = [scores[i] for i in order]
    return out
