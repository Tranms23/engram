"""Engram MCP server: stdio JSON-RPC, zero dependencies.

    python -m engram.mcp

Wire it into any MCP client:

    {
      "mcpServers": {
        "engram": {
          "command": "python",
          "args": ["-m", "engram.mcp"],
          "env": { "ENGRAM_ROOT": "/path/to/project" }
        }
      }
    }

Tools exposed: remember / recall / correct / verify / stats.

Protocol: newline-delimited JSON-RPC 2.0 over stdin/stdout. stdout carries
protocol messages only; anything human-readable goes to stderr.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .core import Ledger

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "engram"
SERVER_VERSION = "0.1.0"

TOOLS = [
    {
        "name": "engram_remember",
        "description": (
            "Save a durable fact, preference, decision or constraint to long-term memory. "
            "Every memory is stored with its evidence and can be corrected later. "
            "Use for things the user would hate to repeat: rules, IDs, paths, numbers, "
            "decisions that are not derivable from the code."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The assertion, one sentence."},
                "evidence": {"type": "string",
                             "description": "Verbatim quote this came from. Defaults to text."},
                "kind": {"type": "string",
                         "enum": ["constraint", "decision", "preference", "fact", "ephemeral"],
                         "default": "fact"},
                "scope": {"type": "string", "default": "global"},
                "confidence": {"type": "number", "default": 0.8},
            },
            "required": ["text"],
        },
    },
    {
        "name": "engram_recall",
        "description": (
            "Search long-term memory. Returns the most relevant claims with their ids, "
            "confidence, source episode and — if the query contradicts a stored hard fact "
            "(a number, date, path or version) — an explicit hard-mismatch warning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 5},
                "scope": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "engram_correct",
        "description": (
            "Supersede a memory that is wrong or outdated. Requires evidence. "
            "The old claim is retracted but never deleted, so the history stays auditable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string"},
                "text": {"type": "string", "description": "What is true now."},
                "evidence": {"type": "string", "description": "What you checked."},
                "ref": {"type": "string", "description": "file:line, commit, URL."},
            },
            "required": ["claim_id", "text", "evidence"],
        },
    },
    {
        "name": "engram_verify",
        "description": "Check the ledger hash chain and provenance integrity.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "engram_stats",
        "description": "Counts, provenance coverage and conflicts in this memory.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _root() -> Path:
    return Path(os.environ.get("ENGRAM_ROOT") or os.getcwd())


_ledger: Ledger | None = None


def ledger() -> Ledger:
    global _ledger
    if _ledger is None:
        _ledger = Ledger(_root())
    return _ledger


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def call_tool(name: str, args: dict) -> dict:
    lg = ledger()

    if name == "engram_remember":
        text = (args.get("text") or "").strip()
        if not text:
            return _err("text is required")
        evidence = (args.get("evidence") or text).strip()
        ep = lg.add_episode(evidence, actor="agent", kind="evidence",
                            meta={"via": "mcp"})
        claim = lg.add_claim(
            text,
            sources=[ep["id"]],
            kind=args.get("kind", "fact"),
            scope=args.get("scope", "global"),
            confidence=float(args.get("confidence", 0.8)),
        )
        lines = [f"saved {claim['id']} ({claim['kind']}, conf={claim['confidence']})",
                 f"  {claim['text']}",
                 f"  source: {ep['id']}"]
        for cf in lg.conflicts():
            if claim["id"] in (cf["left"], cf["right"]) and not cf["resolved"]:
                lines.append(
                    f"CONFLICT: {cf['left_hard']} vs {cf['right_hard']} — both kept, "
                    f"marked contested. Ask the user which one is right before relying on it.")
        return _ok("\n".join(lines))

    if name == "engram_recall":
        query = (args.get("query") or "").strip()
        if not query:
            return _err("query is required")
        hits = lg.recall(query, k=int(args.get("k", 5)), scope=args.get("scope"))
        if not hits:
            return _ok("(no memory matched)")
        out = []
        for i, h in enumerate(hits, 1):
            c = h["claim"]
            out.append(f"{i}. [{h['score']:.2f}] {c['text']}  "
                       f"({c['id']}, {c['kind']}, conf={c['confidence']}, src={c['sources'][0]})")
            if h.get("warning"):
                out.append(f"   WARNING {h['warning']}")
        return _ok("\n".join(out))

    if name == "engram_correct":
        claim_id = args.get("claim_id", "")
        try:
            lg.get_claim(claim_id)
        except KeyError:
            return _err(f"unknown claim: {claim_id}")
        new = lg.correct(claim_id, args.get("text", ""), args.get("evidence", ""),
                         args.get("ref", ""))
        return _ok(f"corrected {claim_id} -> {new['id']}\n  {new['text']}\n"
                   f"  old claim is now retracted (history kept)")

    if name == "engram_verify":
        rep = lg.verify()
        head = "ok" if rep["ok"] else "FAILED"
        return _ok(f"{head}: {rep['records']} records, "
                   + ("hash chain intact" if rep["ok"] else "; ".join(rep["errors"][:5])))

    if name == "engram_stats":
        return _ok(json.dumps(lg.stats(), ensure_ascii=False, indent=2))

    return _err(f"unknown tool: {name}")


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        result = {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return None if is_notification else {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return None if is_notification else {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params", {})
        try:
            result = call_tool(params.get("name", ""), params.get("arguments", {}) or {})
        except Exception as exc:  # never kill the server on a bad tool call
            result = _err(f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(inp, out) -> None:
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()


def main() -> int:
    print(f"engram mcp server ready (root={_root()})", file=sys.stderr)
    serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
