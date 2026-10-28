"""Render the evaluation metrics as PNG charts (Pillow only, no matplotlib).

    python eval/charts.py

Writes reports/cost.png, reports/safety.png, reports/audit.png
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
W, H = 1200, 680
BG = "#ffffff"
INK = "#15181e"
MUTED = "#6b7280"
GRID = "#e5e7eb"
BLUE = "#2f6fed"
GRAY = "#c7ccd6"
RED = "#e5484d"
GREEN = "#12a594"

FONT_CN = "C:/Windows/Fonts/msyh.ttc"
FONT_CN_B = "C:/Windows/Fonts/msyhbd.ttc"
FONT_MONO = "C:/Windows/Fonts/consola.ttf"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_CN_B if bold else FONT_CN, size)
    except OSError:
        return ImageFont.load_default()


def mono(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_MONO, size)
    except OSError:
        return ImageFont.load_default()


def canvas(title: str, subtitle: str = "") -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((56, 40), title, font=font(32, True), fill=INK)
    if subtitle:
        d.text((56, 84), subtitle, font=font(17), fill=MUTED)
    d.line((56, 118, W - 56, 118), fill=GRID, width=1)
    return img, d


def footnote(d, text: str) -> None:
    d.text((56, H - 40), text, font=font(14), fill=MUTED)


def bar(d, x, y, w, h, color, radius=6):
    if h <= 0 or w <= 0:
        return
    d.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=color)


def chart_cost(m: dict) -> Path:
    rows = m["retrieval"]
    img, d = canvas("每次查询注入的上下文（估算 token）",
                    "记忆从 50 条涨到 2,000 条，engram 的注入量几乎不变")
    left, right = 130, W - 90
    top, bottom = 200, H - 140
    plot_w = right - left
    max_v = max(r["full_dump_tokens_per_query"] for r in rows)
    log_max = math.ceil(math.log10(max_v * 1.5))

    def y_of(v: float) -> float:
        return bottom - (math.log10(max(v, 1)) / log_max) * (bottom - top)

    for p in range(0, log_max + 1):
        yy = bottom - (p / log_max) * (bottom - top)
        d.line((left, yy, right, yy), fill=GRID, width=1)
        label = "1" if p == 0 else f"{10 ** p:,}"
        d.text((left - 16, yy - 9), label, font=font(14), fill=MUTED, anchor="ra")

    d.text((56, top - 36), "token（对数刻度）", font=font(14), fill=MUTED)

    group_w = plot_w / len(rows)
    bw = group_w * 0.22
    for i, row in enumerate(rows):
        cx = left + group_w * (i + 0.5)
        pairs = [
            (row["engram"]["tokens_per_query"], BLUE, "engram"),
            (row["full_dump_tokens_per_query"], GRAY, "全量注入"),
        ]
        for j, (val, color, _name) in enumerate(pairs):
            x = cx - bw * 1.15 + j * bw * 1.25
            y = y_of(val)
            bar(d, x, y, bw, bottom - y, color)
            d.text((x + bw / 2, y - 12), f"{val:,.0f}", font=font(17, True),
                   fill=INK, anchor="mb")
        d.text((cx, bottom + 18), f"{row['size']:,} 条记忆", font=font(17),
               fill=INK, anchor="ma")

    d.text((left, bottom + 72), "● engram（恒定取 top-5）", font=font(16), fill=BLUE)
    d.text((left + 260, bottom + 72), "● 全量注入（把整份记忆塞进上下文）",
           font=font(16), fill=MUTED)
    footnote(d, "合成语料 · 20 次查询均值 · token 按字符数估算，仅用于比较量级")
    out = REPORTS / "cost.png"
    img.save(out)
    return out


def chart_safety(m: dict) -> Path:
    d0 = m["decoy"]
    n = d0["n"]
    img, d = canvas("诱饵攻击：24 条「只有数字是错的」的假记忆",
                    "同样的输入，两种记忆结构给出完全不同的结果")
    left, right = 300, W - 120
    top = 195
    row_h = 138
    rows = [
        ("engram", d0["caught"], n - d0["caught"]),
        ("朴素清单", 0, n),
    ]
    for i, (name, good, bad) in enumerate(rows):
        y = top + i * row_h
        d.text((left - 28, y + 34), name, font=font(21, True), fill=INK, anchor="ra")
        total_w = right - left
        gw = total_w * (good / n)
        bw = total_w * (bad / n)
        bar(d, left, y, gw, 68, GREEN if i == 0 else GRID)
        bar(d, left + gw, y, bw, 68, RED if i == 1 else GRID)
        if good:
            d.text((left + gw / 2, y + 34), f"拦截 {good}", font=font(19, True),
                   fill="#ffffff", anchor="mm")
        if bad:
            d.text((left + gw + bw / 2, y + 34), f"静默吸收 {bad}",
                   font=font(19, True), fill="#ffffff", anchor="mm")

    d.text((56, top + 2 * row_h + 10),
           f"查询侧同样有效：{n}/{n} 次「用错数字去问」都给出了 hard-mismatch 警告",
           font=font(17), fill=MUTED)

    callout_y = top + 2 * row_h + 46
    d.rounded_rectangle((56, callout_y, W - 56, callout_y + 80), radius=10,
                        fill="#fdf3f3", outline="#f5d0d1")
    d.text((80, callout_y + 14), "下一轮会发生什么", font=font(16, True), fill=RED)
    d.text((80, callout_y + 42),
           "普通清单里现在有两个互斥的值，模型会随机挑一个，并且不知道自己挑错了。",
           font=font(17), fill=INK)
    footnote(d, "诱饵构造：原句数字翻倍，其余字词完全不变（30s → 60s）")
    out = REPORTS / "safety.png"
    img.save(out)
    return out


def chart_audit(m: dict) -> Path:
    a = m["audit"]["engram"]
    img, d = canvas("一条记忆，能回答三个问题吗",
                    "同样的内容，存成「句子」还是存成「带出处的断言」")
    qx, ex, nx = 120, 720, 930
    top = 210
    row_h = 84

    d.text((ex, top - 46), "engram", font=font(17, True), fill=MUTED, anchor="ma")
    d.text((nx, top - 46), "朴素清单", font=font(17, True), fill=MUTED, anchor="ma")

    for i, (label, key) in enumerate([("谁说的？", "who"),
                                      ("什么时候？", "when"),
                                      ("后来改过吗？", "corrected")]):
        y = top + i * row_h
        d.text((qx, y + 16), label, font=font(21), fill=INK)
        ok = bool(a[key])
        d.ellipse((ex - 15, y + 8, ex + 15, y + 38), fill=BLUE if ok else "#eceef2")
        if ok:
            d.line((ex - 7, y + 23, ex - 1, y + 31, ex + 9, y + 15),
                   fill="#ffffff", width=3, joint="curve")
        d.ellipse((nx - 15, y + 8, nx + 15, y + 38), fill="#eceef2")
        d.line((nx - 7, y + 23, nx + 7, y + 23), fill=MUTED, width=3)
        d.line((qx, y + row_h - 8, W - 90, y + row_h - 8), fill=GRID, width=1)

    card_y = top + 3 * row_h + 20
    d.rounded_rectangle((qx, card_y, W - 90, card_y + 136), radius=10,
                        fill="#f7f8fa", outline=GRID)
    lines = [
        "cl_9f3a…  decision  conf=0.90   status=retracted",
        "「网关超时设为 30s」",
        "→ 来源  ep_2b1c  2026-10-28 14:02  用户原话",
        "→ 已被  cl_7d41 取代：实测 30s 会断（eval:1）",
    ]
    for k, line in enumerate(lines):
        colour = MUTED if k in (0, 3) else INK
        d.text((qx + 22, card_y + 16 + k * 30), line, font=font(16), fill=colour)

    footnote(d, "朴素清单：把对话追加进一个 txt —— 没有来源字段，也没有纠正记录")
    out = REPORTS / "audit.png"
    img.save(out)
    return out


def main() -> int:
    REPORTS.mkdir(exist_ok=True)
    m = json.loads((REPORTS / "metrics.json").read_text(encoding="utf-8"))
    for path in (chart_cost(m), chart_safety(m), chart_audit(m)):
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
