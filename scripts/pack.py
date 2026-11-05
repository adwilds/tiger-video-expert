# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""发布包：一份内容，四个平台的成稿。

设计原则：**不做「一键发布」。**

平台端的自动发布要处理登录态、风控、二次验证，任何一环变了整套就废，
而废掉的方式是静默的（账号被限流你也不一定知道）。所以这里只做到
「把每个平台该填的字都填好、该剪的地方标出来」，最后那一下点击留给人。

输入 06-publish/post.json（一份核心内容），输出每个平台一个 .md 成稿，
外加一份发布前检查表。平台规则放在 platforms.json，改规则不用改代码。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# 各平台成稿约束。数值是「触发警告」的阈值，不是平台的硬上限——
# 平台规则会变，超过就提示人工确认，而不是替用户判定违规。
PLATFORMS: dict[str, dict] = {
    "douyin": {
        "label": "抖音",
        "title_limit": 55,
        "body_limit": 1000,
        "hashtag_min": 3,
        "hashtag_max": 5,
        "aspect": "9:16",
        "cover_aspect": "3:4",
        "duration_sweet": [45, 90],
        "caption_burn_in": True,
        "notes": [
            "前 3 秒必须给出结论，抖音的完播率断点在这里",
            "字幕必须烧进画面：多数人静音滑",
            "话题 3-5 个，第一个放垂类词，最后一个放泛流量词",
        ],
    },
    "xhs": {
        "label": "小红书",
        "title_limit": 20,
        "body_limit": 1000,
        "hashtag_min": 5,
        "hashtag_max": 10,
        "aspect": "9:16",
        "cover_aspect": "3:4",
        "duration_sweet": [30, 60],
        "caption_burn_in": True,
        "notes": [
            "标题 20 字内，用「数字 + 反差」结构最容易点开",
            "正文首行是副标题，别写「大家好」",
            "封面用 3:4，竖版封面在搜索结果里占位更大",
        ],
    },
    "shipinhao": {
        "label": "视频号",
        "title_limit": 16,
        "body_limit": 1000,
        "hashtag_min": 2,
        "hashtag_max": 4,
        "aspect": "9:16",
        "cover_aspect": "9:16",
        "duration_sweet": [60, 180],
        "caption_burn_in": True,
        "notes": [
            "列表页只显示 16 字标题，重点信息必须放进这 16 字",
            "视频号受众年龄偏大，语速可放慢、专有名词要解释",
            "能挂公众号文章就挂，转化路径最短",
        ],
    },
    "x": {
        "label": "X / Twitter",
        "title_limit": None,
        "body_limit": 280,
        "hashtag_min": 0,
        "hashtag_max": 3,
        "aspect": "16:9",
        "cover_aspect": "16:9",
        "duration_sweet": [60, 140],
        "caption_burn_in": True,
        "notes": [
            "英文重写，不要机翻——机翻腔在 X 上是负信号",
            "280 字符是硬约束，超了要拆成 thread",
            "链接放第一条回复，别放正文（会压低曝光）",
        ],
    },
    "bilibili": {
        "label": "B站",
        "title_limit": 80,
        "body_limit": 2000,
        "hashtag_min": 3,
        "hashtag_max": 10,
        "aspect": "16:9",
        "cover_aspect": "16:9",
        "duration_sweet": [180, 600],
        "caption_burn_in": False,
        "notes": [
            "横版为默认，竖版短片走「竖屏」分区",
            "简介里放时间轴，长视频的完播率会好一些",
        ],
    },
}

HASHTAG_RE = re.compile(r"[#＃][^\s#＃]+")


def load_platforms(base_dir: Path | None = None) -> dict:
    """优先读项目里的 platforms.json，没有就用内置默认值。"""
    if base_dir:
        custom = Path(base_dir) / "platforms.json"
        if custom.exists():
            try:
                data = json.loads(custom.read_text(encoding="utf-8"))
                merged = {k: dict(v) for k, v in PLATFORMS.items()}
                for name, cfg in data.items():
                    merged.setdefault(name, {}).update(cfg)
                return merged
            except json.JSONDecodeError:
                pass
    return {k: dict(v) for k, v in PLATFORMS.items()}


def _norm_tags(tags: list[str]) -> list[str]:
    out = []
    for t in tags:
        t = str(t).strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t
        if t not in out:
            out.append(t)
    return out


def _render_body(core: dict, name: str, tags: list[str]) -> str:
    """把核心内容摊成平台正文。不做花活，只保证结构对。

    平台能直接用中文骨架时走默认摊平；X 这类要求本地语言重写的，
    在 post.json 的 bodies 里给专属正文，避免机器翻译腔 + 字数超限。
    """
    override = ((core.get("bodies") or {}).get(name) or "").strip()
    if override:
        if tags and not any(t in override for t in tags):
            override = override.rstrip() + "\n\n" + " ".join(tags)
        return override

    lines: list[str] = []

    hook = (core.get("hook") or "").strip()
    if hook:
        lines.append(hook)
        lines.append("")

    for i, point in enumerate(core.get("key_points") or [], 1):
        lines.append(f"{i}. {point}")
    if core.get("key_points"):
        lines.append("")

    proof = (core.get("proof") or "").strip()
    if proof:
        lines.append(proof)
        lines.append("")

    cta = (core.get("cta") or "").strip()
    if cta:
        lines.append(cta)
        lines.append("")

    if tags:
        lines.append(" ".join(tags))

    return "\n".join(lines).strip()


def _check(plat: dict, title: str, body: str, tags: list[str],
           duration: float | None) -> tuple[list[str], list[str]]:
    errs: list[str] = []
    warns: list[str] = []

    if plat.get("title_limit") and len(title) > plat["title_limit"]:
        errs.append(f"标题 {len(title)} 字，超过 {plat['label']} 的 {plat['title_limit']} 字")
    if len(body) > plat.get("body_limit", 10**9):
        errs.append(f"正文 {len(body)} 字，超过 {plat['body_limit']} 字上限")

    n = len(tags)
    if n < plat.get("hashtag_min", 0):
        warns.append(f"话题只有 {n} 个，建议至少 {plat['hashtag_min']} 个")
    if n > plat.get("hashtag_max", 99):
        warns.append(f"话题 {n} 个，超过建议的 {plat['hashtag_max']} 个，会被判堆砌")

    if duration is not None:
        lo, hi = plat.get("duration_sweet", [0, 10**6])
        if duration < lo:
            warns.append(f"时长 {duration:.0f}s 短于该平台甜点区间 {lo}-{hi}s")
        elif duration > hi:
            warns.append(f"时长 {duration:.0f}s 长于该平台甜点区间 {lo}-{hi}s")

    return errs, warns


def build_package(core: dict, base: Path, out_dir: Path,
                  duration: float | None = None) -> dict:
    """生成全部平台的成稿。返回汇总报告。"""
    base, out_dir = Path(base), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    platforms = load_platforms(base)
    wanted = core.get("platforms") or list(platforms)
    common = _norm_tags(core.get("hashtags_common") or [])
    per_plat_tags = core.get("hashtags") or {}
    tags_override = core.get("hashtags_override") or {}
    titles = core.get("titles") or {}

    report: dict = {"platforms": {}, "generated": []}

    for name in wanted:
        plat = platforms.get(name)
        if not plat:
            report["platforms"][name] = {"errors": [f"未知平台 {name}，先在 platforms.json 里定义"], "warnings": []}
            continue

        title = (titles.get(name) or core.get("title") or "").strip()
        if not title:
            title = (core.get("topic") or "（待填标题）").strip()

        if name in tags_override:
            # 平台有自己的语言/调性，通用标签不并进去（X 上中文标签是负信号）
            tags = _norm_tags(list(tags_override[name]))
        else:
            tags = _norm_tags(list(common) + list(per_plat_tags.get(name) or []))
        body = _render_body(core, name, tags)
        errs, warns = _check(plat, title, body, tags, duration)

        md = [
            f"# {plat['label']} 发布稿",
            "",
            f"**标题**（{len(title)}/{plat.get('title_limit') or '不限'} 字）",
            "",
            f"> {title}",
            "",
            "**正文**",
            "",
            body or "（空）",
            "",
            "**画面规格**",
            "",
            f"- 画布 {plat['aspect']}｜封面 {plat.get('cover_aspect', plat['aspect'])}",
            f"- 字幕烧录：{'需要' if plat.get('caption_burn_in') else '不需要'}",
            f"- 建议时长：{plat['duration_sweet'][0]}-{plat['duration_sweet'][1]}s"
            + (f"（本片 {duration:.0f}s）" if duration else ""),
        ]

        cover_text = (core.get("cover_text") or "").strip()
        if cover_text:
            md += ["", f"**封面文案**：{cover_text}"]

        if plat.get("notes"):
            md += ["", "**这个平台的注意点**", ""]
            md += [f"- {n}" for n in plat["notes"]]

        if errs or warns:
            md += ["", "**发布前必须处理**", ""]
            md += [f"- [错误] {e}" for e in errs]
            md += [f"- [提醒] {w}" for w in warns]

        (out_dir / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        report["generated"].append(name)
        report["platforms"][name] = {
            "title_chars": len(title),
            "body_chars": len(body),
            "hashtags": len(tags),
            "errors": errs,
            "warnings": warns,
        }

    checklist = _checklist(report)
    (out_dir / "发布前检查.md").write_text(checklist, encoding="utf-8")
    return report


def _checklist(report: dict) -> str:
    lines = ["# 发布前检查", ""]
    blocked = [n for n, r in report["platforms"].items() if r.get("errors")]
    warned = [n for n, r in report["platforms"].items() if r.get("warnings")]

    if blocked:
        lines.append("## 先修这些，别发")
        lines.append("")
        for n in blocked:
            for e in report["platforms"][n]["errors"]:
                lines.append(f"- **{n}**：{e}")
        lines.append("")
    else:
        lines += ["## 硬性约束：全部通过", ""]

    if warned:
        lines.append("## 可以发，但这些地方会吃亏")
        lines.append("")
        for n in warned:
            for w in report["platforms"][n]["warnings"]:
                lines.append(f"- **{n}**：{w}")
        lines.append("")

    lines += [
        "## 人工确认项（机器判断不了）",
        "",
        "- [ ] 事实：数字、引用、人名、产品版本号有没有编的",
        "- [ ] 承诺：有没有暗示收益、疗效、绝对化用词",
        "- [ ] 肖像：画面里出现的人是否都同意出镜",
        "- [ ] BGM：是否商用可用（平台曲库内 / 已授权）",
        "- [ ] 封面文字：在小屏上能不能一眼读完",
        "- [ ] 首帧：静止 3 秒内有没有信息量",
        "- [ ] 链接：落地页能不能打开、要不要登录",
        "",
        "## 发布顺序建议",
        "",
        "1. 先发抖音（流量最大，先拿基础数据）",
        "2. 2 小时后发小红书（避免自己和自己抢初始流量池）",
        "3. 同天发视频号（社交推荐靠转发，需要时间发酵）",
        "4. X 隔天发（时区错开，欧美用户在你的夜间活跃）",
        "",
    ]
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法: pack.py <post.json> [--duration 秒] [--out 目录]", file=sys.stderr)
        return 2
    src = Path(argv[1]).resolve()
    core = json.loads(src.read_text(encoding="utf-8"))

    duration = None
    out_dir = src.parent / "out"
    for i, a in enumerate(argv):
        if a == "--duration" and i + 1 < len(argv):
            duration = float(argv[i + 1])
        if a == "--out" and i + 1 < len(argv):
            out_dir = Path(argv[i + 1])

    report = build_package(core, src.parent, out_dir, duration)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    blocked = sum(1 for r in report["platforms"].values() if r.get("errors"))
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(_main(__import__("sys").argv))
