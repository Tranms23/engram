import json
import shutil
import unittest
import uuid
from pathlib import Path

from engram import Ledger, hard_tokens
from engram import bench as bench_mod


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(".tmp") / f"t{uuid.uuid4().hex[:10]}"
        self._tmp.mkdir(parents=True, exist_ok=True)
        self.root = self._tmp
        self.lg = Ledger(self.root)
        self.ep = self.lg.add_episode("用户：图片长边压到 1280px", actor="user")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestProvenance(Base):
    def test_claim_requires_source(self):
        with self.assertRaises(ValueError):
            self.lg.add_claim("图片压到 1280px", sources=[])

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            self.lg.add_claim("图片压到 1280px", sources=["ep_nope"])

    def test_provenance_coverage_is_total(self):
        self.lg.add_claim("图片长边压到 1280px", [self.ep["id"]], kind="preference")
        self.assertEqual(self.lg.stats()["provenance_coverage"], 1.0)


class TestChain(Base):
    def test_chain_ok(self):
        self.lg.add_claim("图片长边压到 1280px", [self.ep["id"]], kind="preference")
        self.assertTrue(self.lg.verify()["ok"])

    def test_tamper_is_detected(self):
        self.lg.add_claim("图片长边压到 1280px", [self.ep["id"]], kind="preference")
        path = self.lg.path
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        lines[-1]["body"]["text"] = "图片长边压到 1920px"
        path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                        encoding="utf-8")
        report = Ledger(self.root).verify()
        self.assertFalse(report["ok"])
        self.assertTrue(any("hash mismatch" in e for e in report["errors"]))


class TestMerging(Base):
    def test_similar_claims_merge(self):
        a = self.lg.add_claim("图片长边压到 1280px 再传给模型", [self.ep["id"]], kind="preference")
        b = self.lg.add_claim("图片长边压到 1280px 再传给模型", [self.ep["id"]], kind="preference")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.lg.claims()), 1)

    def test_hard_conflict_is_contested_not_merged(self):
        a = self.lg.add_claim("图片长边压到 1280px 再传给模型", [self.ep["id"]], kind="preference")
        b = self.lg.add_claim("图片长边压到 1920px 再传给模型", [self.ep["id"]], kind="preference")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self.lg.conflicts()), 1)
        statuses = {c["status"] for c in self.lg.claims()}
        self.assertEqual(statuses, {"contested"})

    def test_different_topic_does_not_conflict(self):
        self.lg.add_claim("图片长边压到 1280px", [self.ep["id"]], kind="preference")
        self.lg.add_claim("密码长度至少 12 位", [self.ep["id"]], kind="constraint")
        self.assertEqual(len(self.lg.conflicts()), 0)

    def test_hard_tokens(self):
        self.assertIn("1280px", hard_tokens("压到 1280px"))
        self.assertIn("2026-11-15", hard_tokens("截止 2026-11-15"))
        self.assertIn("3.12", hard_tokens("Python 3.12"))


class TestCorrection(Base):
    def test_correction_retracts_and_keeps_history(self):
        a = self.lg.add_claim("图片长边压到 1280px", [self.ep["id"]], kind="preference")
        b = self.lg.correct(a["id"], "图片长边压到 1920px", "实测 1280 会糊", "test:1")
        self.assertEqual(b["supersedes"], [a["id"]])
        self.assertEqual(self.lg.get_claim(a["id"])["status"], "retracted")
        self.assertEqual(self.lg.get_claim(b["id"])["status"], "active")
        # history survives
        self.assertEqual(len(self.lg.claims(include_retracted=True)), 2)
        self.assertEqual(self.lg.claims(), [self.lg.get_claim(b["id"])])


class TestRecall(Base):
    def test_relevance_beats_noise(self):
        retry = self.lg.add_claim("重试策略：4xx 只重试 429，最多 3 次",
                                  [self.ep["id"]], kind="decision", confidence=0.9)
        self.lg.add_claim("每日 09:00 跑一次归档", [self.ep["id"]], kind="decision")
        hits = self.lg.recall("重试策略是什么")
        self.assertEqual(hits[0]["claim"]["id"], retry["id"])

    def test_warns_on_hard_mismatch(self):
        self.lg.add_claim("图片长边压到 1280px", [self.ep["id"]], kind="preference")
        hits = self.lg.recall("图片长边压到 1920px")
        self.assertTrue(hits[0]["warning"])
        self.assertIn("1280px", hits[0]["warning"])

    def test_retracted_hidden_by_default(self):
        a = self.lg.add_claim("图片长边压到 1280px", [self.ep["id"]], kind="preference")
        self.lg.correct(a["id"], "图片长边压到 1920px", "实测", "test:2")
        texts = [h["claim"]["text"] for h in self.lg.recall("图片 1280px")]
        self.assertNotIn("图片长边压到 1280px", texts)
        texts_all = [h["claim"]["text"] for h in self.lg.recall("图片 1280px", include_retracted=True)]
        self.assertIn("图片长边压到 1280px", texts_all)


class TestBench(unittest.TestCase):
    def test_no_silent_merge(self):
        report = bench_mod.run()
        self.assertEqual(report["silent_merge"], 0)
        self.assertEqual(report["false_memory_rate"], 0.0)
        self.assertGreaterEqual(report["decoys"], 5)
        self.assertGreater(report["warned_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
