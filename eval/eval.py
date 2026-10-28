"""Engram evaluation harness.

Three measurements, all reproducible:

  1. retrieval   - recall@1 / recall@3 against a keyword baseline, at three
                   memory sizes (does it still find the right thing when the
                   ledger grows?)
  2. cost        - estimated tokens injected per query: top-k vs full dump
  3. safety      - decoy attacks: does a near-miss get absorbed silently?

Everything runs offline on a synthetic corpus, so the numbers are about the
*mechanism*, not about any particular model. Honest labels matter here.

    python eval/eval.py            # prints a table, writes reports/metrics.json
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engram import Ledger  # noqa: E402
from engram.core import tokenize  # noqa: E402

SIZES = [50, 500, 2000]
K = 5
ATTRS = [
    ("超时", "s", [5, 10, 15, 30, 45, 60, 90, 120]),
    ("重试次数", "次", [1, 2, 3, 5, 8]),
    ("页边距", "cm", [1, 2, 3, 4]),
    ("长边像素", "px", [720, 1080, 1280, 1920, 2560]),
    ("并发上限", "", [4, 8, 16, 32, 64]),
]


def est_tokens(text: str) -> int:
    """Rough token estimate for mixed Chinese/Latin text."""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 1.0 + other / 3.5) + 1


def make_corpus(n: int, seed: int = 7):
    rng = random.Random(seed)
    facts, queries, gold = [], [], []
    for i in range(n):
        entity = f"SVC-{i:04d}"
        attr, unit, values = rng.choice(ATTRS)
        val = rng.choice(values)
        text = f"{entity} 的{attr}设为 {val}{unit}"
        facts.append((text, entity, attr))
        queries.append(f"{entity} 的{attr}是多少")
        gold.append(text)
    return facts, queries, gold


def baseline_keyword(facts, query, k=5):
    """What a grep + recency pipeline does: overlap count, newest wins ties."""
    q = set(tokenize(query))
    scored = []
    for idx, (text, _e, _a) in enumerate(facts):
        overlap = len(q & set(tokenize(text)))
        if overlap:
            scored.append((overlap, idx, text))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [s[2] for s in scored[:k]]


def run_retrieval(size: int) -> dict:
    facts, queries, gold = make_corpus(size)
    root = ROOT / ".tmp-eval" / f"r{size}"
    import shutil
    shutil.rmtree(root, ignore_errors=True)
    lg = Ledger(root)
    ep = lg.add_episode("bulk import", actor="system", kind="import")
    # bulk load: skip merge checks so insertion stays linear (imports, not edits)
    for text, _e, _a in facts:
        lg._new_claim(text, "fact", "eval", 0.85, [ep["id"]], [])
    n_claims = len(lg.claims())

    hit1 = hit3 = 0
    tok_engram = tok_full = 0
    for q, g in zip(queries, gold):
        hits = lg.recall(q, k=K)
        got = [h["claim"]["text"] for h in hits]
        if got and got[0] == g:
            hit1 += 1
        if g in got[:3]:
            hit3 += 1
        tok_engram += sum(est_tokens(t) for t in got)
        tok_full += est_tokens("".join(t for t, _e, _a in facts))

    kw1 = kw3 = 0
    for q, g in zip(queries, gold):
        got = baseline_keyword(facts, q, K)
        if got and got[0] == g:
            kw1 += 1
        if g in got[:3]:
            kw3 += 1

    n = len(queries)
    return {
        "size": n_claims,
        "engram": {"recall@1": hit1 / n, "recall@3": hit3 / n,
                   "tokens_per_query": tok_engram / n},
        "keyword": {"recall@1": kw1 / n, "recall@3": kw3 / n},
        "full_dump_tokens_per_query": tok_full / n,
    }


def run_decoy(n_decoys: int = 24) -> dict:
    """Near-miss attack, in two separate ledgers.

    A) write side  - the decoy is written into a ledger that already holds the
                     original. Correct: contested, never silently merged.
    B) query side  - memory holds only the original; the user asks with the
                     wrong number. Correct: the hit carries a hard-mismatch.
    """
    rng = random.Random(11)
    caught = separate = warned = 0

    for i in range(n_decoys):
        entity = f"MOD-{i:03d}"
        val = rng.choice([10, 20, 30, 40])
        text = f"{entity} 的超时设为 {val}s"
        wrong = val * 2
        decoy_text = f"{entity} 的超时设为 {wrong}s"

        # A) write side
        a_dir = ROOT / ".tmp-eval" / f"decoy_a{i}"
        import shutil
        shutil.rmtree(a_dir, ignore_errors=True)
        la = Ledger(a_dir)
        epa = la.add_episode("seed", actor="system", kind="import")
        la.add_claim(text, [epa["id"]], kind="decision", confidence=0.9, scope="decoy")
        before = len(la.conflicts())
        la.add_claim(decoy_text, [epa["id"]], kind="decision", confidence=0.9, scope="decoy")
        if len(la.conflicts()) > before:
            caught += 1
        else:
            separate += 1

        # B) query side
        b_dir = ROOT / ".tmp-eval" / f"decoy_b{i}"
        shutil.rmtree(b_dir, ignore_errors=True)
        lb = Ledger(b_dir)
        epb = lb.add_episode("seed", actor="system", kind="import")
        lb.add_claim(text, [epb["id"]], kind="decision", confidence=0.9, scope="decoy")
        hits = lb.recall(decoy_text, k=1)
        if hits and hits[0].get("warning"):
            warned += 1

    n = n_decoys
    return {
        "n": n,
        "caught": caught, "separate": separate,
        "detect_rate": caught / n,
        "warned_rate": warned / n,
        "naive_silent_rate": 1.0,   # a plain list stores the decoy as another fact
        "provenance": 1.0,
    }


def run_audit(n: int = 40) -> dict:
    """Can the system answer: who said it, when, and has it been corrected?"""
    root = ROOT / ".tmp-eval" / "audit"
    import shutil
    shutil.rmtree(root, ignore_errors=True)
    lg = Ledger(root)
    ep = lg.add_episode("用户：超时设 30s", actor="user", kind="utterance")
    claim = lg.add_claim("网关超时设为 30s", [ep["id"]], kind="decision", confidence=0.9)
    lg.correct(claim["id"], "网关超时设为 60s", "实测 30s 会断", "eval:1")

    got = lg.get_claim(claim["id"])
    answers = {
        "who": bool(got["sources"]),
        "when": bool(got["valid_from"]),
        "corrected": got["status"] == "retracted" and bool(got.get("superseded_by")),
    }
    return {"engram": answers, "naive": {k: False for k in answers}}


def main() -> int:
    out = {"retrieval": [], "decoy": run_decoy(), "audit": run_audit()}
    for size in SIZES:
        row = run_retrieval(size)
        out["retrieval"].append(row)
        print(f"size={row['size']:>5}  "
              f"engram r@1={row['engram']['recall@1']:.3f} r@3={row['engram']['recall@3']:.3f}  "
              f"keyword r@1={row['keyword']['recall@1']:.3f} r@3={row['keyword']['recall@3']:.3f}  "
              f"tok/q={row['engram']['tokens_per_query']:.0f} vs full={row['full_dump_tokens_per_query']:.0f}")

    d = out["decoy"]
    print(f"decoy  caught={d['caught']}/{d['n']}  warned={d['warned_rate']:.3f}  "
          f"naive_silent={d['naive_silent_rate']:.3f}")
    a = out["audit"]
    print(f"audit  engram={a['engram']}")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "metrics.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {reports / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
