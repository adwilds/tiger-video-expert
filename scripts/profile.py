# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""身份卡：先确定"我是谁"，再谈选题。

    python profile.py init          在工作区根建 profile.json（模板）
    python profile.py check         校验必填项，并重新生成 身份卡.md
    python profile.py show          打印当前身份卡

设计约束：
- **profile.json 是唯一真源**，身份卡.md 由它生成，不许手改 md。
  （同一份信息写两处，迟早不一致——草稿目录那次已经吃过这个亏。）
- 校验要**敢拦**。目标观众写成"年轻人""上班族"，后面所有判断都会飘，
  这种值直接算不通过，不做兼容。
- 身份卡是账号级的，放工作区根，不随某一期变化。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROFILE_FILE = "profile.json"
DOC_FILE = "身份卡.md"

# 目标观众写成这些词等于没写：不说清是谁，选题就没有靶心
GENERIC_AUDIENCE = {
    "年轻人", "上班族", "大家", "所有人", "用户", "小白", "普通人", "大众",
    "各年龄段", "职场人", "宝妈", "学生", "网民", "粉丝", "观众",
}

GOALS = {
    "涨粉": "先要人，暂时不变现",
    "带货": "内容为成交服务",
    "引流": "把人导到私域或产品",
    "品牌": "做行业里的信任状",
}

PLATFORM_NAMES = {
    "douyin": "抖音",
    "xhs": "小红书",
    "shipinhao": "视频号",
    "x": "X",
    "bilibili": "B站",
    "youtube": "YouTube",
    "kuaishou": "快手",
}

TEMPLATE = {
    "_说明": "改这个文件就行；身份卡.md 由它生成，不要手改 md。填完跑 profile.py check",
    "account": "",
    "track": [],
    "audience": {"who": "", "pain": ""},
    "goal": "",
    "cadence": "",
    "voice": "",
    "banned": [],
    "platforms": [],
    "updated": "",
}


# ---------------------------------------------------------------- 读写

def profile_path(root: Path) -> Path:
    return root / PROFILE_FILE


def load(root: Path) -> dict | None:
    p = profile_path(root)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(TEMPLATE))
    merged.update(data)
    merged["audience"] = {**TEMPLATE["audience"], **(data.get("audience") or {})}
    return merged


def save(root: Path, profile: dict) -> None:
    profile = dict(profile)
    profile["updated"] = date.today().isoformat()
    profile_path(root).write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------- 校验

def check(profile: dict) -> tuple[list[str], list[str]]:
    """返回 (拦下来的问题, 只是提醒的问题)。"""
    bad: list[str] = []
    warn: list[str] = []

    if not (profile.get("account") or "").strip():
        bad.append("account（账号名）没填")

    track = [t for t in (profile.get("track") or []) if str(t).strip()]
    if len(track) < 3:
        bad.append(
            f"track（赛道关键词）只有 {len(track)} 个，至少 3 个——"
            "情报打分的赛道匹配全靠它，太少就等于没筛"
        )

    audience = profile.get("audience") or {}
    who = (audience.get("who") or "").strip()
    if not who:
        bad.append("audience.who（目标观众）没填")
    elif who in GENERIC_AUDIENCE:
        bad.append(f"audience.who 写的是「{who}」，这不是观众，是人群标签")
    elif len(who) < 6:
        bad.append(f"audience.who 只有「{who}」，要写到职业 + 处境 + 正在为什么发愁")

    if not (audience.get("pain") or "").strip():
        bad.append("audience.pain（他在愁什么）没填")

    goal = (profile.get("goal") or "").strip()
    if not goal:
        bad.append("goal（内容目标）没填")
    elif goal not in GOALS:
        bad.append(f"goal 只能是 {' / '.join(GOALS)}，现在是「{goal}」")

    platforms = [p for p in (profile.get("platforms") or []) if str(p).strip()]
    if not platforms:
        bad.append("platforms（发哪些平台）没填")
    unknown = [p for p in platforms if p not in PLATFORM_NAMES]
    if unknown:
        warn.append(f"platforms 里有工具不认识的平台：{'、'.join(unknown)}（不会报错，pack 会跳过）")

    if not (profile.get("cadence") or "").strip():
        warn.append("cadence（更新频率）没填——它决定情报要抓多勤")
    if not (profile.get("voice") or "").strip():
        warn.append("voice（口吻）没填——写脚本时没依据，容易写成通稿")
    if not (profile.get("banned") or []):
        warn.append("banned（禁区）是空的——写清楚不碰什么，比写清楚要什么更省事")

    return bad, warn


# ---------------------------------------------------------------- 生成人读版

def render_md(profile: dict) -> str:
    track = "、".join(profile.get("track") or []) or "（未填）"
    platforms = "、".join(
        PLATFORM_NAMES.get(p, p) for p in (profile.get("platforms") or [])
    ) or "（未填）"
    audience = profile.get("audience") or {}
    goal = profile.get("goal") or "（未填）"
    banned = profile.get("banned") or []

    lines = [
        f"# 身份卡 · {profile.get('account') or '（未填账号名）'}",
        "",
        "> 本文件由 `profile.json` 生成，**不要手改**。改 json，然后跑 `python scripts/pipeline.py profile check`。",
        f"> 最后更新：{profile.get('updated') or '—'}",
        "",
        "## 我是谁",
        "",
        "| | |",
        "|---|---|",
        f"| 账号 | {profile.get('account') or '（未填）'} |",
        f"| 赛道关键词 | {track} |",
        f"| 内容目标 | {goal} — {GOALS.get(goal, '（自定义）')} |",
        f"| 更新频率 | {profile.get('cadence') or '（未填）'} |",
        f"| 发布平台 | {platforms} |",
        "",
        "## 说给谁听",
        "",
        f"- **是谁**：{audience.get('who') or '（未填）'}",
        f"- **他在愁什么**：{audience.get('pain') or '（未填）'}",
        "",
        "> 这两行决定选题能不能过第一关。写不出具体的人，就是还没有选题。",
        "",
        "## 怎么说",
        "",
        f"- **口吻**：{profile.get('voice') or '（未填）'}",
        "",
        "**禁区**：",
        "",
    ]
    if banned:
        lines += [f"- {b}" for b in banned]
    else:
        lines.append("- （未填）")

    lines += [
        "",
        "> 禁区比要求更值得写。说清不碰什么，能挡掉大部分返工。",
        "",
        "## 这份身份卡在后面的作用",
        "",
        "| 阶段 | 它约束什么 |",
        "|---|---|",
        "| 情报采集 | 赛道关键词决定抓回来的东西筛不筛得动 |",
        "| 选题排序 | 赛道匹配占 35% 权重，是五项里最重的一项 |",
        "| 写脚本 | 口吻决定句子长短和用不用第一人称 |",
        "| 判断取舍 | 内容目标决定「涨粉向」还是「成交向」，两者结构不一样 |",
        "| 分发 | 平台清单决定 pack 出几版稿子 |",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 子命令

def cmd_init(args) -> int:
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    p = profile_path(root)

    if p.exists() and not args.force:
        print(f"{p} 已存在，没覆盖。要重建加 --force")
        return 1

    save(root, json.loads(json.dumps(TEMPLATE)))
    (root / DOC_FILE).write_text(render_md(load(root)), encoding="utf-8")

    print(f"已生成: {p}")
    print(f"        {root / DOC_FILE}")
    print()
    print("填这几项（其余可留空）：")
    print("  account            账号名")
    print("  track              赛道关键词，至少 3 个，写你会长期做的")
    print("  audience.who       具体到职业 + 处境，不接受「年轻人」")
    print("  audience.pain      他在为什么发愁")
    print(f"  goal               只能是 {' / '.join(GOALS)}")
    print("  platforms          douyin / xhs / shipinhao / x / bilibili")
    print()
    print("填完跑: python scripts/pipeline.py profile check")
    return 0


def cmd_check(args) -> int:
    root = Path(args.root).resolve()
    profile = load(root)
    if profile is None:
        print(f"找不到 {profile_path(root)}，先跑 pipeline.py profile init", file=sys.stderr)
        return 1

    bad, warn = check(profile)
    (root / DOC_FILE).write_text(render_md(profile), encoding="utf-8")

    if bad:
        print(f"身份卡还没填完（{len(bad)} 项不通过）：", file=sys.stderr)
        for b in bad:
            print(f"  ✗ {b}", file=sys.stderr)
        if warn:
            print("另外几项只是提醒：", file=sys.stderr)
            for w in warn:
                print(f"  · {w}", file=sys.stderr)
        print(f"\n改 {profile_path(root)}，改完重跑 check", file=sys.stderr)
        return 1

    print(f"身份卡通过：{profile['account']}｜{profile['goal']}｜{'、'.join(profile['track'])}")
    if warn:
        print("仍可补的几项：")
        for w in warn:
            print(f"  · {w}")
    print(f"已重写 {root / DOC_FILE}")
    print("\n下一步：python scripts/pipeline.py intel fetch")
    return 0


def cmd_show(args) -> int:
    root = Path(args.root).resolve()
    profile = load(root)
    if profile is None:
        print(f"找不到 {profile_path(root)}，先跑 pipeline.py profile init", file=sys.stderr)
        return 1
    print(render_md(profile))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="profile", description="身份卡：我是谁、说给谁听")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="建 profile.json 模板")
    p.add_argument("--root", default=".", help="工作区根目录")
    p.add_argument("--force", action="store_true", help="已存在也重建")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="校验必填项并重写身份卡.md")
    p.add_argument("--root", default=".", help="工作区根目录")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("show", help="打印身份卡")
    p.add_argument("--root", default=".", help="工作区根目录")
    p.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
