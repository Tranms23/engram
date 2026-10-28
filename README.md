# Engram

**Memory you can audit.** 可审计的记忆——每条记忆都能回答：谁说的、什么时候、错了证据在哪。

```
pip install -e .
engram ep "把图片长边压到 1280px 再传"
engram add "图片长边压到 1280px" --source ep_xxx --kind preference
engram recall "图片要压多大"
engram bench
```

零依赖（纯标准库），记忆就是**一个 JSONL 文件**，能 `git diff`、能 `cat`、能手改（然后被 `verify` 抓出来）。

---

## 为什么又做一个记忆项目

因为现有的都在解决「怎么记得多」，没人解决「**记错了怎么办**」。

- 向量库：相似就召回，语义近邻当事实用；
- 分层记忆插件：层数越加越多，没有一条能追溯来源；
- 大部分"AI 记忆"方案：写进去就再也改不掉，或者改了没留痕。

而认知科学告诉我们一件不舒服的事：**记忆随时间必然语义化，语义化必然产生假记忆**。Krenz et al., *Nat Commun* 14:6037 (2023) 实测：28 天后，人对**语义相近但没发生过**的事的误报率显著上升，而细节记忆下降——遗忘了细节，留下了要点，要点还会吸收没发生的近邻。

把这个结论搬到 AI 上就是：**任何做了摘要/合并/压缩的记忆系统，都在批量生产幻觉性记忆，而且它自己不知道。**

Engram 的立场：**记忆应该是账本，不是数据库。**

| | 数据库 | 账本（Engram） |
|---|---|---|
| 写入 | 覆盖 | 只追加 |
| 删除 | 真删 | 标记作废，原文不朽 |
| 校验 | 无 | hash 链可验 |
| 错记 | 改掉 | 追加一条纠正 + 证据 |
| 审计 | 靠日志 | 天生的 |

---

## 四条不变量

1. **只追加 + hash 链**
   每条记录带 `prev` 和 `hash`，`engram verify` 能抓出任何一行被改过。

2. **无源不入**
   `add_claim` 必须给 `--source`，指向一条真实存在的 episode。没有来源的断言直接抛错。
   *记忆的门槛不是"重要"，是"可追溯"。*

3. **硬事实不合并**
   数字、日期、版本号、路径——这些东西改动一个字符，语义就变了。
   两条 claim 主题相似但硬事实不同时，**不合并、不裁决**，双双标记 `contested` 并记一条 `conflict`。

4. **检索自带警告**
   你问「是不是 1920px」，而记忆里是 1280px 时，命中结果会带：

   ```
   !! hard-mismatch: query says ['1920px'], memory says ['1280px']
   ```

   不替你裁决，只告诉你对不上。

---

## 数据模型

三层，全部落在同一个 `ledger.jsonl`：

```
episode   原始事件，不可变            ep_xxx
  └── claim      从事件提炼的断言       cl_xxx  (sources → [ep_xxx], hard → [...])
        └── correction  一条新 claim + 证据，supersedes 旧 claim
```

一条 claim：

```json
{
  "id": "cl_9f3a…", "text": "图片长边压到 1280px",
  "kind": "preference", "scope": "work", "confidence": 0.9,
  "sources": ["ep_2b1c…"], "hard": ["1280px"],
  "valid_from": "2026-10-29T00:00:00+00:00", "valid_until": null,
  "supersedes": [], "conflicts_with": [], "status": "active", "rev": 1
}
```

`kind` 决定半衰期（`constraint` 十年、`decision` 五年、`preference` 一年、`fact` 半年、`ephemeral` 两周）。衰减是**指数**的，不是线性掉分——`0.5 ** (age / halflife)`。

## 命令

| 命令 | 作用 |
|---|---|
| `engram ep <text>` | 记一条原始事件 |
| `engram add <text> --source ep_…` | 记一条断言（必须有来源） |
| `engram correct <id> <text> --evidence <text>` | 纠正：追加新断言 + 证据，旧条目转 `retracted` |
| `engram recall <query>` | 检索，输出打分理由、来源、硬事实警告 |
| `engram verify` | 校验 hash 链、来源完整性、悬空引用 |
| `engram stats` | 计数、来源覆盖率 |
| `engram bench` | 跑诱饵基准，测自己的假记忆率 |
| `engram log --all` | 列全部断言（含已作废） |

## 内置红队：`engram bench`

自带 10 条记忆，自动生成「语义相同、数字不同」的诱饵，测两种失败：

```
Engram decoy benchmark
----------------------
claims            10
decoys             8
silent merges      0   (false memory, want 0)
contested          8
false-memory rate 0.000
contest rate      1.000
warned rate       1.000
provenance        1.000
```

- **silent merge = 假记忆**：诱饵被当成原事实吸收。目标恒为 0。
- **contest rate**：冲突被识别并标记的比例。
- **warned rate**：你用错数字去问时，系统是否警告你。

跑不过的提交不许合并。**没有度量的记忆系统不算系统。**

## 和现有方案的差别

| | Engram | Hindsight | 分层记忆插件 | 手搓脚本 |
|---|---|---|---|---|
| 形态 | 单文件账本 + CLI | 云 / 自托管服务 | 插件 + SQLite | 脚本 |
| 来源链 | 强制，逐条可查 | 有 Correction，无逐条溯源 | 一般没有 | 无 |
| 冲突 | 并列 + contested | 新压旧 | 覆盖 | 覆盖 |
| 自测假记忆 | 内置 `bench` | 有基准，但测的是成本/纠正次数 | 无 | 无 |
| 依赖 | 零 | 服务端 | SQLite | — |

Engram 不跟 Hindsight 抢「大而全」。它小、离线、可读、可审计——**适合塞进任何 agent，也适合当记忆的"取证工具"**。

## 非目标

- 不做人格/陪伴（那是另一层，别混进事实层）；
- 不做向量库（v0.1 用 BM25；嵌入是可选的增强，不是地基）；
- 不自动裁决冲突（**记忆系统没有资格替你决定哪个是真的**）。

## Roadmap

- [ ] v0.2 可选嵌入召回（`--embed`，默认关，保持零依赖）
- [ ] v0.2 `engram export --md`：导出人能读的记忆页
- [ ] v0.3 MCP server：`recall` / `remember` / `correct` 三个工具
- [ ] v0.3 DSH / Claude Code 钩子适配（SessionStart 注入、Stop 写回）
- [ ] v0.4 冲突裁决 CLI：人工 `resolve`，记录裁决人与理由
- [ ] v1.0 规范冻结 + 多语言实现（TS / Go）

## 开发

```bash
python -m unittest discover -s tests    # 14 tests
python -m engram bench                  # 红队自检
```

MIT.
