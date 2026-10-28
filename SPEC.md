# Engram 规范 v0.1

本文件是 Engram 的正式定义。实现可以换语言、换存储，但**必须**满足这里的不变量；否则它不是 Engram。

---

## 0. 范围

Engram 定义一种**面向 AI agent 的可审计记忆**：

- 输入：原始事件（episode）、从事件提炼的断言（claim）、纠正（correction）；
- 输出：带来源与警告的检索结果、可校验的账本；
- 不做：人格、对话管理、模型调用、冲突的自动裁决。

## 1. 术语

| 术语 | 定义 |
|---|---|
| **Episode** | 一次不可变的原始事件。文本、时间、actor、kind。 |
| **Claim** | 从 ≥1 个 episode 提炼出的原子断言，带置信度、范围、有效期。 |
| **Hard token** | 数字、日期、版本号、路径。改动即改变语义的 token。 |
| **Correction** | 一条新的 claim，`supersedes` 旧 claim，并携带一条证据 episode。 |
| **Contested** | 两条 claim 主题相似但硬事实冲突，系统拒绝裁决时的状态。 |
| **Ledger** | 追加写的记录序列，每条记录包含前一条的 hash。 |

## 2. 记录格式与链式校验

每条记录是一行 JSON：

```json
{"seq": 3, "type": "claim", "at": "2026-10-29T00:00:00+00:00",
 "prev": "<前一条的 hash>", "hash": "<sha256(prev + canonical(body))>",
 "body": { … }}
```

- `canonical(body)` = `json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`
- 首条记录的 `prev` = 字符串 `"genesis"`。
- 任何 `seq` 不连续、`prev` 不匹配、`hash` 重算不符 → 账本判为**不可信**。

> 这是 Engram 唯一允许的"删除"：删除意味着校验失败，而不是数据消失。

## 3. 不变量

| # | 不变量 | 强制点 |
|---|---|---|
| I1 | 记录只增不改，链式 hash 连续 | `Ledger._write` / `verify` |
| I2 | 每条 claim 至少一个 source，且 source 必须存在 | `add_claim` |
| I3 | 硬事实冲突不得自动合并，必须 `contested` | `add_claim` / `_contest` |
| I4 | 纠正必须携带证据，且旧 claim 转 `retracted` 而非删除 | `correct` |
| I5 | 检索结果必须可解释（分值、命中词、来源、警告） | `recall` |
| I6 | 基准中 silent-merge 恒为 0 | `bench` / CI |

## 4. Claim 状态机

```
        ┌──────────── correct ───────────►  retracted
active ─┤                                    (superseded_by)
        └── hard conflict ──► contested ──► active（人工 resolve 后，v0.4）
                                 │
                                 └── correct ──► retracted
```

- `retracted` 默认不出现在检索结果中，`include_retracted=True` 可取回。
- `contested` 参与检索但权重 ×0.85，且结果必须带冲突提示。

## 5. 合并与冲突

写入 claim 时，对同 `scope` 同 `kind` 的现有 claim 计算 token Jaccard 相似度 `sim`：

```
硬事实集合相同 且 sim ≥ 0.80  → 合并：沿用原 id，sources 并集，confidence = max，rev + 1
硬事实集合不同 且 sim ≥ 0.50  → 冲突：新建 claim 标 contested，原 claim 也标 contested，
                                 追加一条 conflict 记录
否则                          → 新建独立 claim
```

阈值选择的理由：

- 0.80：中文短句的 bigram Jaccard 在"同义改写"上普遍 >0.8，在"主题相关但不同事"上普遍 <0.5；
- 0.50：宁可多标冲突，不可漏标。**误报冲突的代价是一次人工确认，漏报的代价是一条假记忆活到下次检索。**

已知取舍：`"限 30 天"` 与 `"限 30 天，含节假日"` 会被判为冲突（硬事实集合不同）。这是有意的——细化也应该被看见。

## 6. 检索

BM25（k1=2.2, b=0.75），语料为 claim 文本 + tags。

权重：

```
score = bm25 × (0.5 + 0.5 × confidence) × freshness × status_factor

freshness     = 0.5 ^ (age_days / halflife[kind])
status_factor = 1.0 active / 0.85 contested / 0.3 archived
```

半衰期（天）：`constraint 3650`、`decision 1825`、`preference 365`、`fact 180`、`ephemeral 14`。

**硬事实警告**：若查询本身含硬 token 且与命中的 claim 不一致，结果必须带
`hard-mismatch: query says [...], memory says [...]`。

## 7. 纠正协议

`correct(claim_id, text, evidence, evidence_ref)` 必须：

1. 落一条 `kind="evidence"` 的 episode，`meta.for_claim` 指向被纠对象；
2. 新建 claim：`supersedes=[claim_id]`，sources 至少含该证据 episode；
3. 旧 claim 追加一条修订记录：`status="retracted"`、`superseded_by`。

因此"为什么现在这么认为"永远可以回答，而"以前认为什么"永不丢失。

## 8. 诱饵基准

对 fixture 中每条含数字的记忆，生成一个"语义相同、数值不同"的诱饵，测两件事：

- **A. 合并安全**：把诱饵写进已含原记忆的账本 → 必须产生 conflict，silent merge 记 0 分；
- **B. 查询警告**：用诱饵的措辞检索 → 命中必须带 hard-mismatch。

指标：`false_memory_rate`（越低越好，恒为 0）、`contest_rate`、`warned_rate`（越高越好）、`provenance_coverage`（恒为 1）。

## 9. 失败语义

| 情况 | 行为 |
|---|---|
| claim 无 source | 抛 `ValueError`，不写盘 |
| source 不存在 | 抛 `ValueError`，不写盘 |
| 账本被篡改 | `verify()` 返回 `ok=false` + 行号，**不自动修复** |
| 硬事实冲突 | 写盘，标 contested，返回新 claim |
| 检索无命中 | 返回空列表，不编造 |

**不自动修复**是刻意的：自动修复会销毁证据。

## 10. 版本与兼容

- v0.x 允许破坏性变更，但每次变更必须同步更新本文件与 `bench`。
- 记录格式带 `type` 字段，未知 `type` 必须被忽略而非报错（前向兼容）。
- 一个账本文件对应一个 `scope` 树；跨 scope 的合并是 v1 之后的话题。
