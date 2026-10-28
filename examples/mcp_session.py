"""Print a real MCP session against the engram server (in-process, no subprocess).

    python examples/mcp_session.py
"""

import io
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engram import mcp  # noqa: E402

ROOT = Path(__file__).with_name("mcp-memory")


def show(msg: dict) -> None:
    print("→", json.dumps(msg, ensure_ascii=False))


def run(messages: list[dict]) -> list[dict]:
    inp = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    out = io.StringIO()
    mcp.serve(inp, out)
    return [json.loads(x) for x in out.getvalue().splitlines()]


def main() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True)
    os.environ["ENGRAM_ROOT"] = str(ROOT)
    mcp._ledger = None

    print("=== 握手 ===")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "demo"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    for m in msgs:
        show(m)
    for r in run(msgs):
        tools = r.get("result", {}).get("tools", [])
        if tools:
            print("← tools:", ", ".join(t["name"] for t in tools))
        else:
            print("←", json.dumps(r, ensure_ascii=False)[:160])

    print("\n=== 模型决定记住一条 ===")
    m = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "engram_remember",
                    "arguments": {"text": "图片长边压到 1280px", "kind": "preference",
                                  "evidence": "用户：太大了会超限，压到 1280 吧"}}}
    show(m)
    for r in run([m]):
        print("←", r["result"]["content"][0]["text"])

    print("\n=== 另一轮里模型来查 ===")
    m = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "engram_recall", "arguments": {"query": "图片要压多大"}}}
    show(m)
    for r in run([m]):
        print("←", r["result"]["content"][0]["text"])

    print("\n=== 用户说了错数字，模型来问 ===")
    m = {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "engram_recall", "arguments": {"query": "不是 1920px 吗"}}}
    show(m)
    for r in run([m]):
        print("←", r["result"]["content"][0]["text"])

    shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
