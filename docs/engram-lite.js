/* Engram Lite — the same rules as engram/core.py, in ~150 lines of browser JS.
 *
 * This is a demo engine for the landing page: tokenisation, hard-token
 * extraction, conflict detection, status-aware recall. The Python package is
 * the reference implementation; if the two disagree, Python wins.
 */
(function (global) {
  "use strict";

  const HALFLIFE = { constraint: 3650, decision: 1825, preference: 365, fact: 180, ephemeral: 14 };
  const MERGE_SIM = 0.8, CONFLICT_SIM = 0.5;
  const STOP = new Set("的了是在和与就都而及或一个我你他她它们这那有没不也很把被让给对从".split(""));

  function tokenize(s) {
    const low = String(s).toLowerCase();
    const out = [];
    for (const w of low.match(/[a-z]+|[0-9]+/g) || []) out.push(w);
    for (const run of low.match(/[\u4e00-\u9fff]+/g) || []) {
      if (run.length === 1) { if (!STOP.has(run)) out.push("u:" + run); continue; }
      for (let i = 0; i < run.length - 1; i++) out.push("b:" + run.slice(i, i + 2));
      for (const ch of run) if (!STOP.has(ch)) out.push("u:" + ch);
    }
    return out;
  }

  const PATTERNS = [
    /\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?/g,
    /\b[vV]?\d+\.\d+(?:\.\d+)?\b/g,
    /[A-Za-z]:\\[^\s，。；、）)]+/g,
    /(?:\/[\w.\-]+){2,}\/?/g,
  ];
  const NUM = /\d+(?:\.\d+)?\s?(?:px|kb|mb|gb|ms|fps|dpi|%|元|天|次|条|人|行)?/g;

  function hardTokens(text) {
    let rest = String(text);
    const found = [];
    for (const p of PATTERNS) {
      for (const m of rest.match(p) || []) found.push(m);
      rest = rest.replace(p, " ");
    }
    for (const m of rest.match(NUM) || []) found.push(m);
    return [...new Set(found.map((x) => x.trim().toLowerCase()).filter(Boolean))].sort();
  }

  function jaccard(a, b) {
    const A = new Set(a), B = new Set(b);
    if (!A.size || !B.size) return 0;
    let inter = 0;
    for (const x of A) if (B.has(x)) inter++;
    return inter / (A.size + B.size - inter);
  }

  function estTokens(s) {
    let cjk = 0;
    for (const ch of s) if (/[\u4e00-\u9fff]/.test(ch)) cjk++;
    return Math.round(cjk + (s.length - cjk) / 3.5) + 1;
  }

  function id(prefix) {
    return prefix + "_" + Math.random().toString(16).slice(2, 10);
  }

  function createStore(seed) {
    const claims = [];
    const conflicts = [];
    let seq = 0;

    function add(opts) {
      const text = String(opts.text || "").trim();
      if (!text) throw new Error("text is required");
      const kind = opts.kind || "fact";
      const scope = opts.scope || "global";
      const confidence = opts.confidence == null ? 0.8 : opts.confidence;
      const hard = hardTokens(text);
      const sources = [opts.source || id("ep")];
      const now = Date.now();

      for (const c of claims) {
        if (c.status === "retracted" || c.kind !== kind || c.scope !== scope) continue;
        const sim = jaccard(tokenize(text), tokenize(c.text));
        const sameHard = JSON.stringify(hard) === JSON.stringify(c.hard);
        if (sim >= MERGE_SIM && sameHard) {
          c.sources = [...new Set([...c.sources, ...sources])];
          c.confidence = Math.max(c.confidence, confidence);
          return { claim: c, merged: true, conflict: null };
        }
        if (sim >= CONFLICT_SIM && !sameHard) {
          const clash = { id: id("cf"), left: c.id, right: null, leftHard: c.hard, rightHard: hard };
          const fresh = mk(text, kind, scope, confidence, sources, hard, now, "contested");
          clash.right = fresh.id;
          c.status = "contested";
          c.conflictsWith.push(fresh.id);
          fresh.conflictsWith.push(c.id);
          claims.push(fresh);
          conflicts.push(clash);
          return { claim: fresh, merged: false, conflict: clash };
        }
      }
      const fresh = mk(text, kind, scope, confidence, sources, hard, now, "active");
      claims.push(fresh);
      return { claim: fresh, merged: false, conflict: null };
    }

    function mk(text, kind, scope, confidence, sources, hard, now, status) {
      return {
        id: id("cl"), text, kind, scope,
        confidence: Math.round(confidence * 100) / 100,
        sources, hard, status, conflictsWith: [], supersedes: [],
        createdAt: now, rev: 1, seq: ++seq,
      };
    }

    function correct(claimId, text, evidence) {
      const old = claims.find((c) => c.id === claimId);
      if (!old) throw new Error("unknown claim: " + claimId);
      // A correction bypasses merge/conflict detection: the user already decided.
      const fresh = mk(text, old.kind, old.scope, Math.max(old.confidence, 0.9),
                       [id("ep")], hardTokens(text), Date.now(), "active");
      fresh.supersedes = [old.id];
      claims.push(fresh);
      old.status = "retracted";
      old.supersededBy = fresh.id;
      old.evidence = evidence || "";
      return { old, claim: fresh };
    }

    function recall(query, k) {
      k = k || 5;
      const docs = claims.filter((c) => c.status !== "retracted");
      if (!docs.length) return [];
      const corpus = docs.map((c) => tokenize(c.text));
      const n = corpus.length;
      const avgdl = corpus.reduce((a, d) => a + d.length, 0) / n || 1;
      const df = new Map();
      for (const d of corpus) for (const t of new Set(d)) df.set(t, (df.get(t) || 0) + 1);

      const q = [...new Set(tokenize(query))];
      const qh = hardTokens(query);
      const hits = [];
      docs.forEach((c, i) => {
        const tf = new Map();
        for (const t of corpus[i]) tf.set(t, (tf.get(t) || 0) + 1);
        const dl = corpus[i].length || 1;
        let bm = 0;
        const terms = [];
        for (const t of q) {
          if (!tf.has(t)) continue;
          const idf = Math.log(1 + (n - df.get(t) + 0.5) / (df.get(t) + 0.5));
          const f = tf.get(t);
          bm += idf * (f * 2.2) / (f + 1.2 * (0.25 + 0.75 * dl / avgdl));
          terms.push(t);
        }
        if (!bm) return;
        const ageDays = (Date.now() - c.createdAt) / 86400000;
        const fresh = Math.pow(0.5, ageDays / (HALFLIFE[c.kind] || 180));
        let w = bm * (0.5 + 0.5 * c.confidence) * fresh;
        if (c.status === "contested") w *= 0.85;
        const mismatch = qh.length && c.hard.length &&
          JSON.stringify(qh) !== JSON.stringify(c.hard);
        hits.push({
          claim: c, score: Math.round(w * 1000) / 1000, terms,
          warning: mismatch
            ? "hard-mismatch: query says " + JSON.stringify(qh) + ", memory says " + JSON.stringify(c.hard)
            : null,
        });
      });
      hits.sort((a, b) => b.score - a.score);
      return hits.slice(0, k);
    }

    function stats() {
      const by = (f) => claims.reduce((m, c) => ((m[f(c)] = (m[f(c)] || 0) + 1), m), {});
      return {
        claims: claims.length,
        active: claims.filter((c) => c.status === "active").length,
        contested: claims.filter((c) => c.status === "contested").length,
        retracted: claims.filter((c) => c.status === "retracted").length,
        conflicts: conflicts.length,
        kinds: by((c) => c.kind),
      };
    }

    function fullTokens() {
      return claims.reduce((a, c) => a + estTokens(c.text), 0);
    }

    if (seed) for (const s of seed) add(s);

    return { add, correct, recall, stats, fullTokens, claims, conflicts, tokenize, hardTokens, estTokens };
  }

  const api = { createStore, tokenize, hardTokens, estTokens };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.EngramLite = api;
})(typeof window !== "undefined" ? window : globalThis);
