"""Engram core: an append-only, auditable memory ledger.

Invariants (see SPEC.md):
  1. Append-only + hash chain   -> verifiable, tamper-evident
  2. No claim without sources   -> provenance is mandatory
  3. Hard facts never auto-merge -> conflicts are surfaced, not resolved
  4. Retrieval is explainable   -> every hit reports why it matched

A "memory" here is a single JSONL file you can `git diff`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIR = ".engram"
LEDGER_NAME = "ledger.jsonl"

# --- tokenisation ---------------------------------------------------------

_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")

# Function characters carry no topical signal; keeping them would drown BM25.
_CJK_STOP = set("的了是在和与就都而及或一个我你他她它们这那有没不也很把被让给对从")


def tokenize(text: str) -> list[str]:
    """Latin words + CJK bigrams (b:) and content unigrams (u:).

    Bigrams carry precision, unigrams carry recall — Chinese has no spaces and we
    refuse to take a segmentation dependency.
    """
    low = text.lower()
    toks = _WORD.findall(low)
    for run in _CJK.findall(low):
        if len(run) == 1:
            if run not in _CJK_STOP:
                toks.append("u:" + run)
            continue
        toks.extend("b:" + run[i : i + 2] for i in range(len(run) - 1))
        toks.extend("u:" + ch for ch in run if ch not in _CJK_STOP)
    return toks


# --- hard facts -----------------------------------------------------------

_DATE = re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?")
_VERSION = re.compile(r"\b[vV]?\d+\.\d+(?:\.\d+)?\b")
_WINPATH = re.compile(r"[A-Za-z]:\\[^\s，。；、）)]+")
_POSIXPATH = re.compile(r"(?:/[\w.\-]+){2,}/?")
_NUM = re.compile(r"\d+(?:\.\d+)?\s?(?:px|kb|mb|gb|ms|fps|dpi|%|元|天|次|条|人|行)?")


def hard_tokens(text: str) -> list[str]:
    """Numbers, dates, versions, paths: the things a paraphrase must never change."""
    rest = text
    found: list[str] = []
    for pat in (_DATE, _VERSION, _WINPATH, _POSIXPATH):
        found.extend(m.group(0) for m in pat.finditer(rest))
        rest = pat.sub(" ", rest)
    found.extend(m.group(0) for m in _NUM.finditer(rest))
    return sorted({f.strip().lower() for f in found if f.strip()})


def is_hard_conflict(a: str, b: str) -> bool:
    """Same topic (high lexical overlap) but different hard values."""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    if overlap < 0.5:
        return False
    ha, hb = set(hard_tokens(a)), set(hard_tokens(b))
    return bool(ha or hb) and ha != hb


# --- helpers --------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(body: dict) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# Half-life in days: how fast a memory of this kind goes stale.
HALFLIFE = {
    "constraint": 3650.0,
    "decision": 1825.0,
    "preference": 365.0,
    "fact": 180.0,
    "ephemeral": 14.0,
}

MERGE_SIM = 0.80      # above this (and same hard tokens) -> merge into one claim
CONFLICT_SIM = 0.50   # above this (but different hard tokens) -> contested


class Ledger:
    """Append-only memory ledger with a hash chain over every record."""

    def __init__(self, root: str | os.PathLike = "."):
        self.root = Path(root)
        self.path = self.root / DEFAULT_DIR / LEDGER_NAME

    # -- raw io -----------------------------------------------------------

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _write(self, record: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        exists = self.path.exists() and self.path.stat().st_size > 0
        prev = self._read()[-1]["hash"] if exists else "genesis"
        record["prev"] = prev
        record["hash"] = _sha(prev + _canon(record["body"]))
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(_canon(record) + "\n")
        return record

    def append(self, rtype: str, body: dict) -> dict:
        records = self._read()
        return self._write(
            {"seq": len(records) + 1, "type": rtype, "at": _now(), "body": body}
        )

    # -- views ------------------------------------------------------------

    def records(self) -> list[dict]:
        return self._read()

    def episodes(self) -> list[dict]:
        return [r["body"] for r in self._read() if r["type"] == "episode"]

    def conflicts(self) -> list[dict]:
        return [r["body"] for r in self._read() if r["type"] == "conflict"]

    def claims(self, include_retracted: bool = False) -> list[dict]:
        """Materialise claims: last revision per id wins (append-only underneath)."""
        by_id: dict[str, dict] = {}
        for r in self._read():
            if r["type"] == "claim":
                c = dict(r["body"])
                c.setdefault("status", "active")
                c["_seq"] = r["seq"]
                by_id[c["id"]] = c
        out = list(by_id.values())
        if not include_retracted:
            out = [c for c in out if c["status"] != "retracted"]
        return sorted(out, key=lambda c: c["_seq"])

    def get_claim(self, claim_id: str, include_retracted: bool = True) -> dict:
        for c in self.claims(include_retracted=include_retracted):
            if c["id"] == claim_id:
                return c
        raise KeyError(f"unknown claim: {claim_id}")

    # -- writing ----------------------------------------------------------

    def add_episode(self, text: str, actor: str = "user", kind: str = "utterance",
                    meta: dict | None = None) -> dict:
        if not text.strip():
            raise ValueError("episode text is empty")
        body = {
            "id": _new_id("ep"),
            "ts": _now(),
            "actor": actor,
            "kind": kind,
            "text": text.strip(),
            "meta": meta or {},
        }
        self.append("episode", body)
        return body

    def add_claim(self, text: str, sources: list[str], kind: str = "fact",
                  scope: str = "global", confidence: float = 0.7,
                  tags: list[str] | None = None,
                  valid_until: str | None = None) -> dict:
        text = text.strip()
        if not text:
            raise ValueError("claim text is empty")
        if not sources:
            raise ValueError("claim requires at least one source episode (no source, no memory)")
        known = {e["id"] for e in self.episodes()}
        missing = [s for s in sources if s not in known]
        if missing:
            raise ValueError(f"unknown source episode(s): {missing}")

        hard = hard_tokens(text)
        for existing in self.claims():
            if existing["scope"] != scope or existing["kind"] != kind:
                continue
            sim = jaccard(tokenize(text), tokenize(existing["text"]))
            old_hard = set(existing.get("hard", []))
            if sim >= MERGE_SIM and set(hard) == old_hard:
                return self._revise(
                    existing,
                    confidence=max(existing["confidence"], confidence),
                    sources=sources,
                )
            if sim >= CONFLICT_SIM and set(hard) != old_hard:
                return self._contest(existing, text, kind, scope, confidence, sources, hard)

        return self._new_claim(text, kind, scope, confidence, sources, hard,
                               tags=tags, valid_until=valid_until)

    def _new_claim(self, text, kind, scope, confidence, sources, hard,
                   tags=None, valid_until=None, supersedes=None, status="active") -> dict:
        body = {
            "id": _new_id("cl"),
            "text": text,
            "kind": kind,
            "scope": scope,
            "confidence": round(min(max(confidence, 0.0), 1.0), 3),
            "sources": list(dict.fromkeys(sources)),
            "hard": hard,
            "tags": tags or [],
            "valid_from": _now(),
            "valid_until": valid_until,
            "supersedes": supersedes or [],
            "conflicts_with": [],
            "status": status,
            "rev": 1,
        }
        self.append("claim", body)
        return body

    def _revise(self, existing: dict, confidence: float, sources: list[str]) -> dict:
        body = dict(existing)
        body.pop("_seq", None)
        body["rev"] = existing.get("rev", 1) + 1
        body["confidence"] = round(min(max(confidence, 0.0), 1.0), 3)
        body["sources"] = list(dict.fromkeys(existing["sources"] + sources))
        self.append("claim", body)
        return body

    def _contest(self, existing: dict, text: str, kind: str, scope: str,
                 confidence: float, sources: list[str], hard: list[str]) -> dict:
        new = self._new_claim(text, kind, scope, confidence, sources, hard,
                              status="contested")
        old = dict(existing)
        old.pop("_seq", None)
        old["status"] = "contested"
        old["conflicts_with"] = list(dict.fromkeys(old.get("conflicts_with", []) + [new["id"]]))
        old["rev"] = existing.get("rev", 1) + 1
        self.append("claim", old)
        self.append("conflict", {
            "id": _new_id("cf"),
            "at": _now(),
            "left": existing["id"],
            "right": new["id"],
            "reason": "hard_fact_mismatch",
            "left_hard": existing.get("hard", []),
            "right_hard": hard,
            "resolved": False,
        })
        return new

    def correct(self, claim_id: str, text: str, evidence: str,
                evidence_ref: str = "", confidence: float = 0.9) -> dict:
        """A correction is a new claim that supersedes the old one, plus its evidence."""
        old = self.get_claim(claim_id)
        ep = self.add_episode(evidence, actor="agent", kind="evidence",
                              meta={"ref": evidence_ref, "for_claim": claim_id})
        new = self._new_claim(
            text=text,
            kind=old["kind"],
            scope=old["scope"],
            confidence=confidence,
            sources=[ep["id"]],
            hard=hard_tokens(text),
            tags=list(dict.fromkeys(old.get("tags", []) + ["correction"])),
            supersedes=[claim_id],
        )
        retired = dict(old)
        retired.pop("_seq", None)
        retired["status"] = "retracted"
        retired["superseded_by"] = new["id"]
        retired["rev"] = old.get("rev", 1) + 1
        self.append("claim", retired)
        return new

    # -- retrieval --------------------------------------------------------

    def recall(self, query: str, k: int = 5, scope: str | None = None,
               include_retracted: bool = False) -> list[dict]:
        """BM25 over claims, weighted by confidence, kind half-life and status."""
        docs = self.claims(include_retracted=include_retracted)
        if scope:
            docs = [c for c in docs if c["scope"] == scope]
        if not docs:
            return []

        corpus = [tokenize(c["text"] + " " + " ".join(c.get("tags", []))) for c in docs]
        n = len(corpus)
        avgdl = sum(len(d) for d in corpus) / n
        df = Counter()
        for d in corpus:
            df.update(set(d))
        q = [t for t in tokenize(query) if t]
        if not q:
            return []

        now = datetime.now(timezone.utc)
        q_hard = set(hard_tokens(query))
        scored = []
        for claim, doc in zip(docs, corpus):
            tf = Counter(doc)
            dl = len(doc) or 1
            bm25 = 0.0
            hits = []
            for term in set(q):
                if term not in tf:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                f = tf[term]
                bm25 += idf * (f * 2.2) / (f + 1.2 * (0.25 + 0.75 * dl / avgdl))
                hits.append(term)
            if bm25 == 0:
                continue
            weight = 0.5 + 0.5 * claim["confidence"]
            weight *= self._freshness(claim, now)
            if claim["status"] == "contested":
                weight *= 0.85
            if claim["status"] == "archived":
                weight *= 0.3
            warning = None
            if q_hard and claim.get("hard") and q_hard != set(claim["hard"]):
                warning = (f"hard-mismatch: query says {sorted(q_hard)}, "
                           f"memory says {claim['hard']}")
            scored.append({
                "claim": claim,
                "score": round(bm25 * weight, 4),
                "raw": round(bm25, 4),
                "warning": warning,
                "why": {"terms": sorted(hits), "confidence": claim["confidence"],
                        "freshness": round(self._freshness(claim, now), 3)},
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]

    @staticmethod
    def _freshness(claim: dict, now: datetime) -> float:
        try:
            born = datetime.fromisoformat(claim["valid_from"])
        except (KeyError, ValueError):
            return 0.5
        age = max((now - born).total_seconds() / 86400.0, 0.0)
        half = HALFLIFE.get(claim["kind"], 180.0)
        return 0.5 ** (age / half)

    # -- integrity --------------------------------------------------------

    def verify(self) -> dict:
        records = self._read()
        errors: list[str] = []
        prev = "genesis"
        for i, r in enumerate(records, 1):
            if r.get("seq") != i:
                errors.append(f"seq gap at line {i}: {r.get('seq')}")
            if r.get("prev") != prev:
                errors.append(f"broken link at line {i}")
            expect = _sha(prev + _canon(r["body"]))
            if r.get("hash") != expect:
                errors.append(f"hash mismatch at line {i}")
            prev = r.get("hash")

        episodes = {e["id"] for e in self.episodes()}
        for c in self.claims(include_retracted=True):
            if not c.get("sources"):
                errors.append(f"claim {c['id']} has no sources")
            for s in c.get("sources", []):
                if s not in episodes:
                    errors.append(f"claim {c['id']} cites missing episode {s}")
            for s in c.get("supersedes", []):
                try:
                    self.get_claim(s)
                except KeyError:
                    errors.append(f"claim {c['id']} supersedes missing {s}")
        return {"ok": not errors, "records": len(records), "errors": errors}

    def stats(self) -> dict:
        claims = self.claims(include_retracted=True)
        by_kind = Counter(c["kind"] for c in claims)
        by_status = Counter(c["status"] for c in claims)
        covered = sum(1 for c in claims if c.get("sources"))
        return {
            "episodes": len(self.episodes()),
            "claims": len(claims),
            "by_kind": dict(by_kind),
            "by_status": dict(by_status),
            "conflicts": len(self.conflicts()),
            "provenance_coverage": round(covered / len(claims), 3) if claims else 1.0,
            "ledger_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }
