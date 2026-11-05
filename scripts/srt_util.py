# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""SRT 读写与时间轴重映射。

粗剪之后字幕必须跟着重映射，否则字幕停在源素材的时间点上，
画面已经剪短了，字幕就会越说越晚、最后整段落在画面外。

映射规则：
- 落在保留区间里的时间点，按 `dst = dst_start + (t - src_start)` 平移
- 落在被剪掉区间里的时间点，吸附到最近的边界（不产生越界字幕）
- 一条字幕跨了多个保留段时，合并成一条覆盖首尾（不重复显示）
"""

from __future__ import annotations

import re

TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _to_sec(m: re.Match) -> float:
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def format_ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def parse_srt(text: str) -> list[dict]:
    """解析 SRT。无法解析的块直接跳过，不臆造时间。"""
    text = (text or "").replace("\r\n", "\n").lstrip("\ufeff")
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        idx = 0
        if re.fullmatch(r"\d+", lines[0].strip()):
            idx = 1
        if idx >= len(lines) or "-->" not in lines[idx]:
            continue
        left, _, right = lines[idx].partition("-->")
        m1, m2 = TS.search(left), TS.search(right)
        if not m1 or not m2:
            continue
        body = " ".join(lines[idx + 1:]).strip()
        if not body:
            continue
        cues.append({"start": _to_sec(m1), "end": _to_sec(m2), "text": body})
    return cues


def format_srt(cues: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(cues, 1):
        blocks.append(f"{i}\n{format_ts(c['start'])} --> {format_ts(c['end'])}\n{c['text']}\n")
    return "\n".join(blocks)


def _mapper(timeline: list[dict]):
    """构造 src -> dst 的映射函数。timeline 需按 src 升序。"""
    spans = [(float(s["src_start"]), float(s["src_end"]), float(s["dst_start"]))
             for s in timeline if float(s["src_end"]) > float(s["src_start"])]
    if not spans:
        return lambda t: t
    last_dst_end = float(timeline[-1]["dst_end"])

    def map_t(t: float) -> float:
        if t <= spans[0][0]:
            return 0.0
        for src_a, src_b, dst_a in spans:
            if src_a <= t <= src_b:
                return dst_a + (t - src_a)
        # 落在被剪掉的区间：吸附到前一段末尾 / 后一段开头，取更近的
        prev_end = None
        next_start = None
        for src_a, src_b, dst_a in spans:
            if src_b < t:
                prev_end = dst_a + (src_b - src_a)
            elif src_a > t and next_start is None:
                next_start = dst_a
        if prev_end is not None and next_start is not None:
            return prev_end if (t - prev_end) <= (next_start - t) else next_start
        return prev_end if prev_end is not None else (
            next_start if next_start is not None else last_dst_end)

    return map_t


def remap_cues(cues: list[dict], timeline: list[dict],
               min_cover: float = 0.35) -> tuple[list[dict], list[str]]:
    """把源时间轴上的字幕搬到新时间轴。返回 (新字幕, 告警)。

    一条字幕如果有超过 `1 - min_cover` 的篇幅落在被剪掉的区间里，
    说明这句话本身就被删了——直接丢弃，不要压成 0.3 秒闪一下。
    """
    warns: list[str] = []
    map_t = _mapper(timeline)
    spans = [(float(s["src_start"]), float(s["src_end"]))
             for s in timeline if float(s["src_end"]) > float(s["src_start"])]

    def covered(s: float, e: float) -> float:
        total = e - s
        if total <= 0:
            return 0.0
        hit = 0.0
        for a, b in spans:
            lo, hi = max(a, s), min(b, e)
            if hi > lo:
                hit += hi - lo
        return hit / total

    out: list[dict] = []
    dropped = 0

    for c in cues:
        if covered(c["start"], c["end"]) < min_cover:
            dropped += 1
            continue
        new_start = map_t(c["start"])
        new_end = map_t(c["end"])
        if new_end <= new_start:
            new_end = new_start + 0.6      # 整条被剪掉时不留零长字幕
        out.append({"start": new_start, "end": new_end, "text": c["text"]})

    # 去掉零长空壳
    out = [c for c in out if c["end"] - c["start"] > 1e-3]

    # 防重叠：后一条不得早于前一条结束
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["end"]:
            out[i]["start"] = out[i - 1]["end"]
            if out[i]["end"] <= out[i]["start"]:
                out[i]["end"] = out[i]["start"] + 0.6

    if dropped:
        warns.append(f"{dropped} 条字幕整句落在被剪掉的位置，已丢弃（不压成闪一下）")
    return out, warns


SPLIT_AT = 32          # 竖屏两行约 2×16 字，和 check_plan 的 SRT_LINE_LIMIT*2 对齐
SPLIT_MIN_DUR = 0.8    # 拆出来的每条至少停这么久，否则宁可让它长一点
_SPLIT_PUNCT = "，,、；;：:"


def _best_cut(text: str) -> int:
    """找最接近中点、且不是句首句尾的标点位置。找不到返回 -1。"""
    mid = len(text) / 2
    best, best_gap = -1, None
    for i, ch in enumerate(text[:-1]):
        if ch not in _SPLIT_PUNCT:
            continue
        cut = i + 1
        if cut >= len(text) - 1:
            continue
        gap = abs(cut - mid)
        if best_gap is None or gap < best_gap:
            best, best_gap = cut, gap
    return best


def split_long_cues(cues: list[dict], limit: int = SPLIT_AT,
                    min_dur: float = SPLIT_MIN_DUR) -> tuple[list[dict], int]:
    """一条字幕超过两行就在标点处拆成两条，时间按字数比例分。

    不拆的话竖屏会顶到边，剪映里再手改是纯体力活。拆不动（没标点、太短）就原样留着，
    让 check_plan 继续报警，不假装干净。
    """
    out: list[dict] = []
    done = 0
    for c in cues:
        text = c["text"].strip()
        if len(text) <= limit:
            out.append(c)
            continue
        cut = _best_cut(text)
        if cut <= 0:
            out.append(c)
            continue
        head, tail = text[:cut].rstrip(), text[cut:].strip()
        total = c["end"] - c["start"]
        if not head or not tail or total <= 0:
            out.append(c)
            continue
        mid = c["start"] + total * len(head) / (len(head) + len(tail))
        if mid - c["start"] < min_dur or c["end"] - mid < min_dur:
            out.append(c)
            continue
        out.append({"start": c["start"], "end": mid, "text": head})
        out.append({"start": mid, "end": c["end"], "text": tail})
        done += 1
    return out, done


def remap_srt(text: str, timeline: list[dict]) -> tuple[str, list[str]]:
    cues = parse_srt(text)
    new_cues, warns = remap_cues(cues, timeline)
    new_cues, split_n = split_long_cues(new_cues)
    if split_n:
        warns.append(f"{split_n} 条字幕超过两行，已按标点拆成两条（时间按字数比例分）")
    return format_srt(new_cues), warns
