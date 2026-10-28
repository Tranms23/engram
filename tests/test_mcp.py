"""MCP server tests: drive the protocol in-process (no subprocess needed)."""

import io
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from engram import mcp


def rpc(method, params=None, req_id=1, notify=False):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        msg["id"] = req_id
    return msg


class MCPBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(".tmp") / f"mcp{uuid.uuid4().hex[:8]}"
        self.root.mkdir(parents=True, exist_ok=True)
        os.environ["ENGRAM_ROOT"] = str(self.root)
        mcp._ledger = None

    def tearDown(self):
        mcp._ledger = None
        os.environ.pop("ENGRAM_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)

    def roundtrip(self, messages):
        inp = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
        out = io.StringIO()
        mcp.serve(inp, out)
        return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]

    def call(self, name, args):
        resp = self.roundtrip([rpc("tools/call", {"name": name, "arguments": args})])[0]
        return resp["result"]


class TestProtocol(MCPBase):
    def test_initialize(self):
        r = self.roundtrip([rpc("initialize", {"protocolVersion": "2024-11-05"})])[0]
        self.assertEqual(r["result"]["serverInfo"]["name"], "engram")
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_notification_gets_no_response(self):
        out = self.roundtrip([rpc("notifications/initialized", notify=True)])
        self.assertEqual(out, [])

    def test_tools_list(self):
        r = self.roundtrip([rpc("tools/list")])[0]
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertEqual(names, {"engram_remember", "engram_recall", "engram_correct",
                                 "engram_verify", "engram_stats"})

    def test_unknown_method(self):
        r = self.roundtrip([rpc("does/not/exist")])[0]
        self.assertEqual(r["error"]["code"], -32601)

    def test_malformed_line_does_not_crash(self):
        inp = io.StringIO("not json\n" + json.dumps(rpc("ping")) + "\n")
        out = io.StringIO()
        mcp.serve(inp, out)
        self.assertEqual(len(out.getvalue().splitlines()), 1)


class TestTools(MCPBase):
    def test_remember_then_recall(self):
        r = self.call("engram_remember", {"text": "图片长边压到 1280px",
                                          "kind": "preference", "confidence": 0.9})
        self.assertFalse(r["isError"])
        self.assertIn("saved cl_", r["content"][0]["text"])

        r = self.call("engram_recall", {"query": "图片要压多大"})
        text = r["content"][0]["text"]
        self.assertIn("1280px", text)
        self.assertIn("cl_", text)

    def test_remember_requires_text(self):
        r = self.call("engram_remember", {})
        self.assertTrue(r["isError"])

    def test_conflict_is_reported(self):
        self.call("engram_remember", {"text": "网关超时设为 30s", "kind": "decision"})
        r = self.call("engram_remember", {"text": "网关超时设为 60s", "kind": "decision"})
        self.assertIn("CONFLICT", r["content"][0]["text"])

    def test_correct_keeps_history(self):
        r1 = self.call("engram_remember", {"text": "网关超时设为 30s", "kind": "decision"})
        cid = [w for w in r1["content"][0]["text"].split() if w.startswith("cl_")][0]

        r2 = self.call("engram_correct", {"claim_id": cid, "text": "网关超时设为 60s",
                                          "evidence": "实测 30s 会断"})
        self.assertIn("retracted", r2["content"][0]["text"])

        r3 = self.call("engram_recall", {"query": "网关超时多少"})
        self.assertIn("60s", r3["content"][0]["text"])

    def test_correct_unknown_claim(self):
        r = self.call("engram_correct", {"claim_id": "cl_nope", "text": "x",
                                         "evidence": "y"})
        self.assertTrue(r["isError"])

    def test_verify_and_stats(self):
        self.call("engram_remember", {"text": "密码长度至少 12 位", "kind": "constraint"})
        self.assertIn("hash chain intact", self.call("engram_verify", {})["content"][0]["text"])
        stats = json.loads(self.call("engram_stats", {})["content"][0]["text"])
        self.assertEqual(stats["claims"], 1)
        self.assertEqual(stats["provenance_coverage"], 1.0)

    def test_unknown_tool(self):
        r = self.call("engram_nope", {})
        self.assertTrue(r["isError"])


if __name__ == "__main__":
    unittest.main()
