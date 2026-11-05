# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""发布前预估：这条脚本大概能跑到什么量级，以及哪里在拖后腿。

    python forecast.py <项目目录> [--platform douyin] [--script 01-script/script.md]

先把话说死，免得误用：

- **这不是播放量预测。** 平台推荐机制不可观测，谁给你一个精确数字都是编的。
- 这里做的是两件事：**结构体检**（钩子/结构/密度/平台适配哪里不行）
  和**基线外推**（拿你自己账号的历史中位数，按结构分上下浮动）。
- 没有历史数据时，**不给绝对数字**。给一个"新号大概几百播放"的经验值，
  听起来像预测，实际是猜——这个工具不干这种事。
- 点赞和评论按你自己的历史互动率推，不用行业平均值，因为每个号的粉性差太远。

结论要这么读：区间中值是你的账号在"内容结构正常"时的常态表现，
不是"发了就会有这么多"。真正决定成败的是选题和表达，这两个都算不出来。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import metrics as metrics_mod  # noqa: E402
import pack as pack_mod        # noqa: E402

CHARS_PER_SEC = 4.5           # 中文口播，和 SKILL.md 一致

CONTRAST = ("其实", "并不是", "不是", "而是", "别再", "错了", "真相", "反而", "恰恰", "根本不")
PUSH = ("但是", "所以", "因为", "然而", "不过", "问题是", "换句话说")
PAYOFF = ("结论", "总之", "最后", "总结", "记住", "这就是", "所以我")
SOURCE = ("据", "来源", "报告", "财报", "官方", "数据显示", "论文")

WEIGHTS = {"hook": 0.30, "struct": 0.25, "density": 0.15, "platform": 0.30}

MIN_SAMPLES = 3               # 少于这个数不给绝对数字


def _clean(text: str) -> str:
    text = re.sub(r"^---[\s\S]*?---", "", text)          # YAML front matter
    text = re.sub(r"```[\s\S]*?```", "", text)           # 代码块
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)   # 标题符号
    text = re.sub(r"[*_>`]", "", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _cjk_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fa50-9A-Za-z]", text))


def _frontmatter(text: str) -> dict:
    """读脚本头部的声明值。字数/时长以它为准，不靠数文件里的字数。

    脚本文件里除了口播正文，还常有钩子备选、素材备注——整篇数字数会把时长估成两三倍，
    平台适配那一项（权重 30%）就废了。
    """
    m = re.match(r"---\s*\n([\s\S]*?)\n---", text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def analyze_script(text: str) -> dict:
    body = _clean(text)
    fm = _frontmatter(text)
    n = _cjk_len(body)
    first_sentence = re.split(r"[。！？!?\n]", body, maxsplit=1)[0][:80]
    head = body[:80]

    declared_chars = re.sub(r"[^0-9.]", "", fm.get("字数", ""))
    declared_secs = re.sub(r"[^0-9.]", "", fm.get("预计时长", ""))
    chars = int(float(declared_chars)) if declared_chars else n
    seconds = round(float(declared_secs), 1) if declared_secs else round(n / CHARS_PER_SEC, 1)
    grain = "脚本头部声明" if declared_chars or declared_secs else "按正文字数估"

    hook = 0
    if "？" in head or "?" in head:
        hook += 30
    if re.search(r"\d", head):
        hook += 25
    if any(w in head for w in CONTRAST):
        hook += 30
    if _cjk_len(first_sentence) <= 40:
        hook += 15
    hook = min(100, hook)

    struct = 0
    if any(w in body for w in PUSH):
        struct += 20
    if any(w in body for w in PAYOFF):
        struct += 20
    if len(re.findall(r"\d+", body)) >= 2:
        struct += 20
    if any(w in body for w in SOURCE):
        struct += 20
    if body.count("\n") >= 2:
        struct += 20
    struct = min(100, struct)

    nums = len(re.findall(r"\d+", body))
    per100 = (nums / max(1, n)) * 100
    density = min(100, int(per100 * 40))          # 每百字 2.5 个数字算满分

    return {
        "chars": chars,
        "seconds": seconds,
        "basis": grain,
        "hook": hook,
        "struct": struct,
        "density": density,
        "first_sentence": first_sentence.strip(),
        "hook_head": head.strip(),
    }


def platform_fit(seconds: float, platform: str) -> tuple[int, str]:
    rule = pack_mod.PLATFORMS.get(platform) or {}
    lo, hi = (rule.get("duration_sweet") or [0, 10 ** 6])[:2]
    if lo <= seconds <= hi:
        return 100, f"{seconds:.0f}s 落在 {lo}-{hi}s 甜点区"
    if seconds < lo:
        gap = (lo - seconds) / max(1, lo)
        return max(0, int(100 - gap * 160)), f"{seconds:.0f}s 比甜点区下限 {lo}s 短 {lo - seconds:.0f}s"
    gap = (seconds - hi) / max(1, hi)
    return max(0, int(100 - gap * 160)), f"{seconds:.0f}s 比甜点区上限 {hi}s 长 {seconds - hi:.0f}s"


def history_base(project: Path, platform: str) -> dict:
    path = project / metrics_mod.METRICS_DIR / "metrics.csv"
    rows = metrics_mod.read_rows([path]) if path.exists() else []
    rows = [r for r in rows if (r.get("platform") or "") == platform and r.get("views")]
    views = [r["views"] for r in rows]
    info = {"n": len(views), "median": None, "like_rate": None, "comment_rate": None}
    if len(views) >= MIN_SAMPLES:
        info["median"] = statistics.median(views)
        lr = [r["likes"] / r["views"] for r in rows if r.get("likes") and r.get("views")]
        cr = [r["comments"] / r["views"] for r in rows if r.get("comments") and r.get("views")]
        info["like_rate"] = statistics.median(lr) if lr else None
        info["comment_rate"] = statistics.median(cr) if cr else None
    return info


def build_report(project: Path, platform: str, script_path: Path) -> tuple[str, dict]:
    text = script_path.read_text(encoding="utf-8")
    a = analyze_script(text)
    fit, fit_note = platform_fit(a["seconds"], platform)
    label = (pack_mod.PLATFORMS.get(platform) or {}).get("label", platform)

    score = round(
        WEIGHTS["hook"] * a["hook"] + WEIGHTS["struct"] * a["struct"]
        + WEIGHTS["density"] * a["density"] + WEIGHTS["platform"] * fit
    )
    k = round(0.6 + score / 100 * 1.2, 2)          # 0.60 ~ 1.80

    base = history_base(project, platform)
    est = None
    if base["median"]:
        mid = base["median"] * k
        est = {
            "low": int(mid * 0.7), "mid": int(mid), "high": int(mid * 1.4),
            "likes": (int(mid * 0.7 * base["like_rate"]), int(mid * 1.4 * base["like_rate"]))
            if base["like_rate"] else None,
            "comments": (int(mid * 0.7 * base["comment_rate"]), int(mid * 1.4 * base["comment_rate"]))
            if base["comment_rate"] else None,
        }

    problems: list[str] = []
    fixes: list[str] = []
    if a["hook"] < 60:
        problems.append(f"钩子 {a['hook']}/100：前 3 秒撑不住")
        fixes.append("开头 40 字内放一个具体问题或一个反常识判断，别铺垫背景")
    if a["struct"] < 60:
        problems.append(f"结构 {a['struct']}/100：推进或兑现段缺东西")
        fixes.append("中段补一个「但是/所以」，结尾补一句回答开头的问题；数字要给出一处")
    if a["density"] < 40:
        problems.append(f"信息密度 {a['density']}/100：几乎没有具体数字")
        fixes.append("每百字至少落一个具体数字或时间点，并标出处")
    if fit < 100:
        problems.append(f"平台适配 {fit}/100：{fit_note}")
        fixes.append(f"{label}的甜点时长是 "
                     f"{'-'.join(map(str, (pack_mod.PLATFORMS.get(platform) or {}).get('duration_sweet', [])))}s，"
                     "按这个调整字数或拆条")

    lines = [
        f"# 发布前预估 · {label}",
        "",
        f"> **这不是播放量预测。** 平台推荐不可观测，任何精确数字都是编的。",
        "> 这里只做两件事：结构体检 + 用你自己账号的历史中位数做外推。",
        "> 区间中值的意思是「结构正常时的常态表现」，不是「发了就会有这么多」。",
        "",
        f"脚本：`{script_path.name}`｜{a['chars']} 字｜约 {a['seconds']:.0f} 秒（{a['basis']}）",
        "",
        "## 结构体检",
        "",
        "| 维度 | 得分 | 权重 | 看什么 |",
        "|---|---|---|---|",
        f"| 钩子 | {a['hook']} | 30% | 前 40 字有没有问题/数字/反差 |",
        f"| 结构 | {a['struct']} | 25% | 推进、兑现、数字、出处、分段 |",
        f"| 信息密度 | {a['density']} | 15% | 每百字几个具体数字 |",
        f"| 平台适配 | {fit} | 30% | 时长是否落在甜点区 |",
        f"| **综合** | **{score}** | | 调整系数 ×{k}（0.60-1.80） |",
        "",
        f"- 开场句：{a['first_sentence'] or '（空）'}",
        f"- 平台：{fit_note}",
        "",
    ]

    if problems:
        lines += ["## 拖后腿的地方", ""]
        lines += [f"- {p}" for p in problems]
        lines += ["", "## 具体怎么改", ""]
        lines += [f"{i}. {f}" for i, f in enumerate(fixes, 1)]
        lines += [""]
    else:
        lines += ["## 结构没问题", "",
                  "四项都在及格线以上。剩下的变量是选题本身和你的表达，这两个工具算不出来。", ""]

    lines += ["## 量级预估", ""]
    if est:
        lines += [
            f"依据：这个平台你已记录 **{base['n']} 条**数据，播放中位数 **{int(base['median'])}**，"
            f"乘结构调整系数 ×{k}。",
            "",
            "| 指标 | 保守 | 中值 | 乐观 |",
            "|---|---|---|---|",
            f"| 播放 | {est['low']} | {est['mid']} | {est['high']} |",
        ]
        if est["likes"]:
            lines.append(f"| 点赞 | {est['likes'][0]} | — | {est['likes'][1]} |")
        if est["comments"]:
            lines.append(f"| 评论 | {est['comments'][0]} | — | {est['comments'][1]} |")
        lines += [
            "",
            "点赞和评论按你历史互动率推（点赞率 "
            f"{(base['like_rate'] or 0) * 100:.1f}%、评论率 {(base['comment_rate'] or 0) * 100:.2f}%），"
            "不是行业平均值——每个号的粉性差太远。",
            "",
            f"算法：`中位播放 × 系数 × [0.7, 1.0, 1.4]`，系数 = 0.6 + 结构分/100 × 1.2。"
            f"公式是明的，你可以自己核对。",
        ]
    else:
        lines += [
            f"**数据不够，不给数字。** 这个平台你只记录了 {base['n']} 条，少于 {MIN_SAMPLES} 条。",
            "",
            "为什么不给：拿「新号一般几百播放」这种经验值凑一个数，看起来像预测，实际是猜。",
            "而且不同账号的粉性差十倍，别人的基线套不到你身上。",
            "",
            "攒数据的办法：",
            "",
            "```bash",
            "python scripts/pipeline.py metrics log . --platform "
            f"{platform} --views 1200 --completion 32% --likes 40 --comments 6",
            "```",
            "",
            f"记满 {MIN_SAMPLES} 条同平台数据，再跑一次这条命令，就会按你自己的基线算。",
        ]

    lines += [
        "",
        "## 这个数不能用来做什么",
        "",
        "- **不能用来决定发不发。** 结构分低但选题对，照样能跑；结构分高但选题平庸，一样没人看。",
        "- **不能用来跟别人比。** 系数和基线都来自你自己的账号，跟别人的号没有可比性。",
        "- **不能用来承诺结果。** 平台改一次推荐权重，所有历史基线一起失效。",
        "",
    ]
    data = {"platform": platform, "score": score, "k": k, "features": a,
            "fit": fit, "base": {kk: vv for kk, vv in base.items()}, "est": est}
    return "\n".join(lines), data


def cmd_forecast(args) -> int:
    project = Path(args.dir).resolve()
    if not (project / "project.json").exists():
        print(f"{project} 不是项目目录（没有 project.json）", file=sys.stderr)
        return 1

    script = project / args.script
    if not script.exists():
        print(f"找不到脚本 {script}", file=sys.stderr)
        print("先写脚本放到 01-script/script.md，或用 --script 指定。", file=sys.stderr)
        print("（纯素材剪辑形态没有脚本，这个功能用不上——它估的是脚本结构。）", file=sys.stderr)
        return 1

    project_cfg = json.loads((project / "project.json").read_text(encoding="utf-8"))
    platforms = [args.platform] if args.platform else (project_cfg.get("platforms") or ["douyin"])

    out_dir = project / "06-publish"
    out_dir.mkdir(parents=True, exist_ok=True)

    blocks, summary = [], []
    for p in platforms:
        text, data = build_report(project, p, script)
        blocks.append(text)
        summary.append((p, data))

    out = out_dir / "预估.md"
    out.write_text("\n\n---\n\n".join(blocks), encoding="utf-8")

    print(f"预估: {out}")
    print()
    for p, d in summary:
        label = (pack_mod.PLATFORMS.get(p) or {}).get("label", p)
        base = d["base"]
        tail = (f"中值 {d['est']['mid']} 播放" if d["est"]
                else f"数据不足（{base['n']}/{MIN_SAMPLES} 条），不给数字")
        print(f"  {label:<8} 结构 {d['score']:>3}/100  ×{d['k']}  {tail}")
    print()
    print("结构分低不等于不能发，结构分高也不等于会火。它只说明这条脚本哪里该改。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forecast", description="发布前预估：结构体检 + 历史基线外推（不是播放量预测）"
    )
    parser.add_argument("dir", help="项目目录")
    parser.add_argument("--platform", default="", help="只估某个平台（默认用 project.json 里的全部）")
    parser.add_argument("--script", default="01-script/script.md", help="脚本相对路径")
    args = parser.parse_args(argv)
    return cmd_forecast(args)


if __name__ == "__main__":
    raise SystemExit(main())
