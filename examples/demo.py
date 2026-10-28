"""A five-minute tour of Engram: episode -> claim -> conflict -> correction -> recall.

Run:  python examples/demo.py
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engram import Ledger  # noqa: E402


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> None:
    root = Path(__file__).with_name("demo-memory")
    shutil.rmtree(root, ignore_errors=True)
    lg = Ledger(root)

    rule("1. 原始事件")
    ep1 = lg.add_episode("图片长边压到 1280px 再传给你，太大了会超限", actor="user")
    print(f"{ep1['id']}  {ep1['text']}")

    rule("2. 提炼成断言（必须有来源）")
    c1 = lg.add_claim("图片长边压到 1280px", [ep1["id"]],
                      kind="preference", scope="work", confidence=0.9)
    print(f"{c1['id']}  {c1['text']}  hard={c1['hard']}")

    rule("3. 用户后来改口 1920px —— 系统不合并，标为冲突")
    ep2 = lg.add_episode("其实 1920px 更清楚，用这个", actor="user")
    c2 = lg.add_claim("图片长边压到 1920px", [ep2["id"]],
                      kind="preference", scope="work", confidence=0.9)
    for cf in lg.conflicts():
        print(f"conflict: {cf['left_hard']} vs {cf['right_hard']}  (unresolved={not cf['resolved']})")
    print(f"两条都在: {c1['id']}={lg.get_claim(c1['id'])['status']}, "
          f"{c2['id']}={lg.get_claim(c2['id'])['status']}")

    rule("4. 用错误的数字去问，检索会警告")
    for hit in lg.recall("图片要压到 1280px 吗"):
        print(f"[{hit['score']:.3f}] {hit['claim']['text']}")
        if hit["warning"]:
            print(f"   !! {hit['warning']}")

    rule("5. 纠正要有证据：旧条目不删除，只作废")
    ep3 = lg.add_episode("实测 1920px 上传会被压回 1280px", actor="agent", kind="evidence")
    fixed = lg.correct(c2["id"], "图片长边压到 1920px，但上传时平台会压回 1280px",
                       "实测：上传后 EXIF 显示 1280", "demo:upload-test")
    print(f"{fixed['id']}  supersedes={fixed['supersedes']}")
    print(f"旧条目状态: {lg.get_claim(c2['id'])['status']}")
    print(f"历史仍在: {len(lg.claims(include_retracted=True))} 条（其中活跃 {len(lg.claims())} 条）")

    rule("6. 账本自检")
    report = lg.verify()
    print(f"hash 链完整: {report['ok']}  记录数: {report['records']}")
    print(f"来源覆盖率: {lg.stats()['provenance_coverage']}")

    rule("7. 记忆就是这一个文件")
    print(lg.path)
    print(f"{lg.path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
