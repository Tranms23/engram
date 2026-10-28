"""Engram CLI: a memory you can inspect, not a black box."""

from __future__ import annotations

import argparse
import json
import sys

from . import bench as bench_mod
from .core import Ledger


def _fmt_claim(c: dict) -> str:
    flags = []
    if c["status"] != "active":
        flags.append(c["status"].upper())
    if c.get("tags"):
        flags.append(",".join(c["tags"]))
    tag = f"  [{' | '.join(flags)}]" if flags else ""
    return f"{c['id']}  {c['kind']:<10} {c['confidence']:.2f}  {c['text']}{tag}"


def cmd_ep(args, ledger: Ledger):
    ep = ledger.add_episode(args.text, actor=args.actor, kind=args.kind)
    print(f"{ep['id']}  {ep['ts']}  {ep['kind']}  {ep['text']}")
    return 0


def cmd_add(args, ledger: Ledger):
    claim = ledger.add_claim(
        args.text,
        sources=args.source,
        kind=args.kind,
        scope=args.scope,
        confidence=args.confidence,
        tags=args.tag or None,
    )
    print(_fmt_claim(claim))
    for c in ledger.conflicts():
        if claim["id"] in (c["left"], c["right"]) and not c["resolved"]:
            print(f"  ! contested: {c['left_hard']} vs {c['right_hard']} "
                  f"(left={c['left']}, right={c['right']})")
    return 0


def cmd_correct(args, ledger: Ledger):
    claim = ledger.correct(args.claim_id, args.text, args.evidence, args.ref)
    print(f"corrected {args.claim_id} -> {claim['id']}")
    print(_fmt_claim(claim))
    return 0


def cmd_recall(args, ledger: Ledger):
    hits = ledger.recall(args.query, k=args.k, scope=args.scope,
                         include_retracted=args.all)
    if not hits:
        print("(no memory matched)")
        return 0
    for i, h in enumerate(hits, 1):
        c = h["claim"]
        print(f"{i}. [{h['score']:.3f}] {c['text']}")
        print(f"   {c['id']}  {c['kind']}  conf={c['confidence']}  "
              f"fresh={h['why']['freshness']}  terms={','.join(h['why']['terms'][:6])}")
        print(f"   sources: {', '.join(c['sources'])}")
        if h.get("warning"):
            print(f"   !! {h['warning']}")
    return 0


def cmd_verify(args, ledger: Ledger):
    report = ledger.verify()
    if report["ok"]:
        print(f"ok: {report['records']} records, hash chain intact")
        return 0
    print(f"FAILED: {len(report['errors'])} problem(s)")
    for e in report["errors"]:
        print(f"  - {e}")
    return 1


def cmd_stats(args, ledger: Ledger):
    print(json.dumps(ledger.stats(), ensure_ascii=False, indent=2))
    return 0


def cmd_log(args, ledger: Ledger):
    for c in ledger.claims(include_retracted=args.all):
        print(_fmt_claim(c))
    return 0


def cmd_bench(args, ledger: Ledger):
    report = bench_mod.run()
    print(bench_mod.render(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["silent_merge"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="engram", description="auditable memory ledger")
    p.add_argument("--root", default=".", help="project root holding .engram/")
    sub = p.add_subparsers(dest="cmd", required=True)

    ep = sub.add_parser("ep", help="record a raw episode")
    ep.add_argument("text")
    ep.add_argument("--actor", default="user")
    ep.add_argument("--kind", default="utterance")
    ep.set_defaults(func=cmd_ep)

    add = sub.add_parser("add", help="record a claim (requires --source)")
    add.add_argument("text")
    add.add_argument("--source", action="append", required=True,
                     help="episode id; repeatable")
    add.add_argument("--kind", default="fact",
                     choices=["constraint", "decision", "preference", "fact", "ephemeral"])
    add.add_argument("--scope", default="global")
    add.add_argument("--confidence", type=float, default=0.7)
    add.add_argument("--tag", action="append")
    add.set_defaults(func=cmd_add)

    cor = sub.add_parser("correct", help="supersede a claim with evidence")
    cor.add_argument("claim_id")
    cor.add_argument("text")
    cor.add_argument("--evidence", required=True)
    cor.add_argument("--ref", default="")
    cor.set_defaults(func=cmd_correct)

    rec = sub.add_parser("recall", help="query memory")
    rec.add_argument("query")
    rec.add_argument("-k", type=int, default=5)
    rec.add_argument("--scope")
    rec.add_argument("--all", action="store_true", help="include retracted")
    rec.set_defaults(func=cmd_recall)

    ver = sub.add_parser("verify", help="check the hash chain and provenance")
    ver.set_defaults(func=cmd_verify)

    st = sub.add_parser("stats", help="counts and coverage")
    st.set_defaults(func=cmd_stats)

    lg = sub.add_parser("log", help="list claims")
    lg.add_argument("--all", action="store_true")
    lg.set_defaults(func=cmd_log)

    bn = sub.add_parser("bench", help="run the decoy benchmark")
    bn.add_argument("--json", action="store_true")
    bn.set_defaults(func=cmd_bench)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args, Ledger(args.root))


if __name__ == "__main__":
    sys.exit(main())
