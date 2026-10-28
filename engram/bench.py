"""Decoy benchmark: does the ledger mistake a near-miss for a fact?

Two independent scenarios per memory:

  A. merge safety   - the decoy is *written* to a ledger that already holds the
                      original. Correct behaviour: contested, never silently merged.
  B. query warning  - a user asks in terms of the wrong number. Correct behaviour:
                      the hit carries a hard-mismatch warning.

A memory system that cannot measure its own false-memory rate is not a system.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from .core import Ledger, hard_tokens

FIXTURE = [
    ("图片长边压到 1280px 再传给模型", "preference", 0.9),
    ("重试策略：4xx 只重试 429，最多 3 次", "decision", 0.95),
    ("报告导出必须是 A4，页边距 2cm", "constraint", 0.95),
    ("Python 版本固定 3.12", "constraint", 0.9),
    ("课程作业提交截止 2026-11-15", "fact", 0.9),
    ("账本放在 .engram/ledger.jsonl", "decision", 0.9),
    ("每次改动前先备份到 backups/ 目录", "preference", 0.85),
    ("API 超时设 30s", "decision", 0.8),
    ("密码长度至少 12 位", "constraint", 0.9),
    ("每日 09:00 跑一次归档", "decision", 0.75),
]

_NUMRUN = re.compile(r"\d+(?:\.\d+)?")


def mutate(text: str) -> tuple[str, str, str] | None:
    """Swap one numeric token for a plausible wrong one."""
    m = _NUMRUN.search(text)
    if not m:
        return None
    raw = m.group(0)
    if "." in raw:
        new = f"{float(raw) * 2:g}"
    else:
        n = int(raw)
        new = str(n * 2 if n < 1900 else n + 1)
    return text[: m.start()] + new + text[m.end():], raw, new


def _seed(ledger: Ledger) -> None:
    for text, kind, conf in FIXTURE:
        ep = ledger.add_episode(f"seed: {text}", actor="user", kind="seed")
        ledger.add_claim(text, [ep["id"]], kind=kind, confidence=conf, scope="bench")


def _scratch() -> str:
    """Writable scratch dir. Some sandboxes deny mkdir inside their own temp dirs,
    so we create a nested path in one shot under ./.tmp instead."""
    d = Path(".tmp") / f"s{uuid.uuid4().hex[:10]}"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def run() -> dict:
    merge_bad = merge_contested = merge_ambiguous = 0
    warned = not_warned = 0
    decoys = 0
    samples: list[dict] = []

    tmp = _scratch()
    try:
        base = f"{tmp}/base"
        _seed(Ledger(base))
        base_ledger = Ledger(base)
        original = {c["text"]: c for c in base_ledger.claims()}

        for text, kind, conf in FIXTURE:
            mutated = mutate(text)
            if not mutated:
                continue
            decoy_text, old, new = mutated
            decoys += 1

            # --- A. merge safety ---
            a_dir = f"{tmp}/a_{decoys}"
            _seed(Ledger(a_dir))
            la = Ledger(a_dir)
            ep = la.add_episode(f"user asserts: {decoy_text}", actor="user")
            before = len(la.conflicts())
            out = la.add_claim(decoy_text, [ep["id"]], kind=kind, confidence=conf, scope="bench")
            after = len(la.conflicts())

            if after > before:
                merge_contested += 1
                verdict_a = "contested"
            elif out["text"] == original[text]["text"] and "correction" not in out:
                merge_bad += 1
                verdict_a = "silent-merge"
            else:
                merge_ambiguous += 1
                verdict_a = "separate"

            # --- B. query warning ---
            b_dir = f"{tmp}/b_{decoys}"
            _seed(Ledger(b_dir))
            lb = Ledger(b_dir)
            hits = lb.recall(decoy_text, k=1)
            got_warning = bool(hits and hits[0].get("warning"))
            if got_warning:
                warned += 1
            else:
                not_warned += 1

            samples.append({
                "original": text,
                "decoy": decoy_text,
                "swap": f"{old} -> {new}",
                "merge": verdict_a,
                "warned": got_warning,
            })

        finals = Ledger(base)
        stats = finals.stats()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = decoys or 1
    return {
        "claims": len(FIXTURE),
        "decoys": decoys,
        "silent_merge": merge_bad,
        "contested": merge_contested,
        "separate": merge_ambiguous,
        "false_memory_rate": round(merge_bad / total, 3),
        "contest_rate": round(merge_contested / total, 3),
        "warned_rate": round(warned / total, 3),
        "provenance_coverage": stats["provenance_coverage"],
        "samples": samples,
    }


def render(report: dict) -> str:
    lines = [
        "Engram decoy benchmark",
        "----------------------",
        f"claims            {report['claims']}",
        f"decoys            {report['decoys']}",
        f"silent merges     {report['silent_merge']}   (false memory, want 0)",
        f"contested         {report['contested']}",
        f"separate          {report['separate']}",
        f"false-memory rate {report['false_memory_rate']:.3f}",
        f"contest rate      {report['contest_rate']:.3f}",
        f"warned rate       {report['warned_rate']:.3f}",
        f"provenance        {report['provenance_coverage']:.3f}",
        "",
        "samples:",
    ]
    for s in report["samples"][:5]:
        lines.append(f"  [{s['merge']:<13}] warn={str(s['warned']):<5} {s['swap']:<14} {s['decoy']}")
    return "\n".join(lines)
