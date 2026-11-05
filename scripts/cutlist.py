# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""转写结果 -> 切点表。

设计前提：两种错误的代价不对称。
  漏删一句废话 → 啰嗦两秒，不影响理解
  误删一个"但是" → 前后因果断裂，且成片里很难听出来
因此本模块输出「保留什么」，不输出「删什么」；默认保留，只有明确命中删除规则才切。

输入  transcript.json（faster-whisper 的 segments 结构，含 start/end/text）
输出  cutlist.json（保留区间 + 删除明细 + 告警）
      切点对照表.md（人工 3 分钟复核用）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

# --- 规则表 -------------------------------------------------------------

# 命中任一即强制保留，即使它看起来像废话
PROTECTED = (
    "但是", "但", "可是", "然而", "不过", "其实", "所以", "因为", "因此", "于是",
    "换句话说", "也就是说", "问题是", "关键是", "重点是", "核心是", "本质是",
    "不是", "并不", "没有", "反而", "恰恰",
)

# 独立出现才删的语气词（作为句子的一部分出现时不删）
FILLERS = ("嗯", "啊", "呃", "那个", "就是说", "然后呢", "对对对", "对的对的")

# 拍摄指令残留
DIRECTIVES = ("等一下", "重来", "卡了", "停一下", "再来一遍", "不好意思", "重新说")

# 自我纠正的开头
SELF_CORRECT = ("不对", "说错了", "不是这样")

SENT_END = "。！？!?；;…"

DEFAULTS = {
    "min_keep_gap": 1.0,      # 间隙小于此值不切开，直接接上（防碎剪）
    "padding": 0.15,          # 保留区间两端外扩，避免咬字
    "silence_target": 0.35,   # 普通停顿压缩后的时长
    "breath_pause": 0.80,     # 思考性停顿保留的时长（不做机械全切）
    "think_pause": 2.5,       # 超过这个长度属真静默，不算思考
    "similar": 0.92,          # 相邻句相似度阈值
    "max_shrink": 0.35,       # 总时长缩短上限，超过则告警
}


@dataclass
class Unit:
    text: str
    start: float
    end: float
    keep: bool = True
    reason: str = ""
    manual: bool = False      # 人工指定（导演分稿标红），算法不得改动


@dataclass
class Result:
    units: list[Unit] = field(default_factory=list)
    keep_ranges: list[tuple[float, float]] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)   # 源区间 -> 新时间轴
    warnings: list[str] = field(default_factory=list)

    @property
    def total_in(self) -> float:
        return self.units[-1].end - self.units[0].start if self.units else 0.0

    @property
    def total_out(self) -> float:
        return self.timeline[-1]["dst_end"] if self.timeline else 0.0


def _norm(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:\"'（）()…—]+", "", text or "")


def _similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _is_bare_filler(text: str) -> bool:
    """整段除语气词和标点外没有实质内容，才算可删语气词。"""
    core = _norm(text)
    if not core:
        return True
    for f in FILLERS:
        core = core.replace(f, "")
    return len(core) <= 1


def _has_protected(text: str) -> str:
    for p in PROTECTED:
        if p in text:
            return p
    return ""


def _is_directive(text: str) -> str:
    for d in DIRECTIVES:
        if d in text:
            return d
    return ""


def classify(units: list[Unit], cfg: dict) -> None:
    """就地标注每个 unit 的 keep / reason。保护规则优先级最高。"""
    for i, u in enumerate(units):
        text = u.text.strip()
        if u.manual:
            u.keep, u.reason = True, "人工指定（导演分稿），算法不改动"
            continue
        if not text:
            u.keep, u.reason = False, "空段"
            continue

        hit = _has_protected(text)
        d = _is_directive(text)
        if d:
            u.keep, u.reason = False, f"拍摄指令「{d}」"
            continue
        if hit:
            u.keep, u.reason = True, f"保护词「{hit}」"
            continue

        if _is_bare_filler(text):
            u.keep, u.reason = False, "独立语气词"
            continue

        if text.startswith(SELF_CORRECT) and len(_norm(text)) < 12:
            u.keep, u.reason = False, "自我纠正的废弃短句"
            continue

        if i > 0 and units[i - 1].keep:
            sim = _similar(text, units[i - 1].text)
            if sim >= cfg["similar"]:
                u.keep, u.reason = False, f"与上一句重复（{sim:.0%}）"
                continue

        u.keep, u.reason = True, "保留"

    _drop_aborted_takes(units, cfg)


def _drop_aborted_takes(units: list[Unit], cfg: dict) -> None:
    """「重来」这类指令前后是同一句时，删前一次，留后一次。

    为什么单独一遍：主循环只看「相邻上一句」，而指令本身被删掉了，
    前后两次录音中间隔着一个 keep=False 的单元，相似度比不到一起，
    结果就是同一句在成片里说两遍——比不剪更难看。

    只在指令前后两句高度相似时才动手，避免把「等一下，我们先看数据」
    这类正常表达的后文误删。
    """
    for i, u in enumerate(units):
        if u.keep or not u.text or not _is_directive(u.text):
            continue
        prev = next((units[j] for j in range(i - 1, -1, -1) if units[j].keep), None)
        nxt = next((units[j] for j in range(i + 1, len(units)) if units[j].keep), None)
        if not prev or not nxt or prev.manual:
            continue
        sim = _similar(prev.text, nxt.text)
        if sim >= cfg["similar"]:
            prev.keep = False
            prev.reason = f"重录，被后一次替代（相似 {sim:.0%}）"
            if nxt.reason in ("保留", ""):
                nxt.reason = "重录后保留"


def _build_timeline(units: list[Unit], cfg: dict) -> tuple[list[dict], list[tuple[float, float]], list[str]]:
    """生成新时间轴：保留段按顺序排列，中间的空隙做压缩而不是丢弃。

    丢弃空隙会让节奏机械（每句话都顶在一起）。正确做法是留呼吸：
    普通停顿压到 silence_target，转折/思考前的停顿留 breath_pause。

    返回 (timeline, keep_ranges, notes)
    """
    keeps = [u for u in units if u.keep]
    if not keeps:
        return [], [], []

    # 1. 真正连续的合并成一段（间隙 <= 0.05s）
    merged: list[dict] = []
    for u in keeps:
        if merged and u.start - merged[-1]["src_end"] <= 0.05:
            merged[-1]["src_end"] = u.end
        else:
            merged.append({"src_start": u.start, "src_end": u.end, "next_text": ""})

    # 记录每段之后紧跟的那句话，用于判断停顿性质
    for i, m in enumerate(merged):
        m["next_text"] = units[0].text if i == 0 else _next_kept_text(units, m["src_end"])

    # 2. 排到新时间轴
    timeline: list[dict] = []
    notes: list[str] = []
    cursor = 0.0
    prev_src_end: float | None = None

    for m in merged:
        if prev_src_end is not None:
            gap = m["src_start"] - prev_src_end
            if gap <= 0.05 or gap < cfg["min_keep_gap"]:
                pause = 0.0                      # 太短，切开是碎剪，直接接上
            elif gap > cfg["think_pause"]:
                pause = cfg["breath_pause"]      # 超长停顿：是真静默，留一点呼吸
                notes.append(
                    f"{prev_src_end:.2f}s–{m['src_start']:.2f}s 有 {gap:.2f}s 长停顿，"
                    f"压到 {pause:.2f}s"
                )
            elif _has_protected(m["next_text"]):
                pause = cfg["breath_pause"]      # 转折前的停顿：多半在思考，别压死
            else:
                pause = cfg["silence_target"]
            cursor += pause

        dur = m["src_end"] - m["src_start"]
        timeline.append({
            "src_start": round(m["src_start"], 3),
            "src_end": round(m["src_end"], 3),
            "dst_start": round(cursor, 3),
            "dst_end": round(cursor + dur, 3),
        })
        cursor += dur
        prev_src_end = m["src_end"]

    keep_ranges = [(t["src_start"], t["src_end"]) for t in timeline]

    if timeline:
        shrink = 1 - (cursor / (units[-1].end - units[0].start))
        if shrink > cfg["max_shrink"]:
            notes.append(
                f"总时长缩短 {shrink:.0%}，超过 {cfg['max_shrink']:.0%} 阈值，大概率切多了，请重点复核"
            )

    return timeline, keep_ranges, notes


def _next_kept_text(units: list[Unit], after: float) -> str:
    for u in units:
        if u.keep and u.start >= after - 0.01:
            return u.text
    return ""


def build(transcript: dict, cfg: dict | None = None) -> Result:
    c = {**DEFAULTS, **(cfg or {})}
    segs = transcript.get("segments") or []
    if not segs:
        raise ValueError("transcript 里没有 segments，检查转写是否成功")

    units = [
        Unit(
            text=(s.get("text") or "").strip(),
            start=float(s["start"]),
            end=float(s["end"]),
            manual=bool(s.get("manual")),
        )
        for s in segs
    ]

    classify(units, c)
    # 首句与末句永删不得
    units[0].keep, units[0].reason = True, "首句，强制保留"
    units[-1].keep, units[-1].reason = True, "末句，强制保留"

    timeline, keep_ranges, notes = _build_timeline(units, c)
    result = Result(units=units, keep_ranges=keep_ranges, timeline=timeline)
    result.warnings.extend(notes)

    if not result.keep_ranges:
        result.warnings.append("没有任何区间被保留，规则可能过严，请人工检查")

    if any(b[0] < a[1] for a, b in zip(result.keep_ranges, result.keep_ranges[1:])):
        result.warnings.append("转写时间戳疑似乱序，请检查 transcript")

    return result


def _fmt(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def to_markdown(result: Result, title: str = "切点对照表") -> str:
    lines = [
        f"# {title}",
        "",
        f"- 原始时长：{result.total_in:.1f}s",
        f"- 粗剪后：{result.total_out:.1f}s"
        f"（缩短 {1 - result.total_out / result.total_in:.0%}）",
        f"- 切点数量：{sum(1 for u in result.units if not u.keep)}",
        "",
        "> 复核方式：只做一件事——看到删错的，把该行的时间码记下来告诉我，我救回。",
        "",
    ]
    if result.warnings:
        lines += ["> ⚠️ " + w for w in result.warnings] + [""]

    lines += ["| # | 时间码 | 状态 | 原文 | 依据 |", "|---|---|---|---|---|"]
    for i, u in enumerate(result.units, 1):
        status = "✅ 保留" if u.keep else "❌ 删除"
        text = u.text.replace("|", "｜")
        lines.append(
            f"| {i} | {_fmt(u.start)}-{_fmt(u.end)} | {status} | {text} | {u.reason} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    import sys

    if len(sys.argv) < 2:
        print("用法: cutlist.py <transcript.json> [输出目录]", file=sys.stderr)
        return 2

    src = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else src.parent
    transcript = json.loads(src.read_text(encoding="utf-8"))

    result = build(transcript)

    cutlist = {
        "total_in": round(result.total_in, 3),
        "total_out": round(result.total_out, 3),
        "shrink": round(1 - result.total_out / result.total_in, 4) if result.total_in else 0,
        "keep_ranges": [
            {"start": round(s, 3), "end": round(e, 3)} for s, e in result.keep_ranges
        ],
        "timeline": result.timeline,
        "cuts": [
            {"start": round(u.start, 3), "end": round(u.end, 3),
             "text": u.text, "reason": u.reason}
            for u in result.units if not u.keep
        ],
        "protected": [
            {"text": u.text, "reason": u.reason}
            for u in result.units if u.keep and u.reason.startswith("保护词")
        ],
        "warnings": result.warnings,
    }

    (out_dir / "cutlist.json").write_text(
        json.dumps(cutlist, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "切点对照表.md").write_text(to_markdown(result), encoding="utf-8")

    print(f"保留 {len(result.keep_ranges)} 段，切掉 {len(cutlist['cuts'])} 段")
    print(f"时长 {result.total_in:.1f}s -> {result.total_out:.1f}s")
    for w in result.warnings:
        print(f"[警告] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
