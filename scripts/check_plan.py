# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""plan.json 交付前校验。

在生成草稿之前跑，拦掉那些"草稿能生成但片子是坏的"的情况：
素材缺失、画布不匹配、时间轴断裂或重叠、总时长与配音对不上。

退出码 0 = 可导出；1 = 有问题，不要导出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

try:
    from pymediainfo import MediaInfo
except ImportError:  # 校验音频时长是可选增强
    MediaInfo = None

# 总时长与配音的允许偏差（秒）。超过这个值说明分镜和音频没对齐。
TOLERANCE = 0.25


# 各平台对时长的容忍上限（秒）。超了不一定发不出去，但完播率会塌。
PLATFORM_DURATION = {
    "douyin": 180,
    "xhs": 300,
    "shipinhao": 1800,
    "x": 140,
    "bilibili": 3600,
}

# 单条字幕超过这个字宽就要断行，否则竖屏上会顶到边
SRT_LINE_LIMIT = 16
# 单条字幕停留超过这个秒数，观众会觉得卡住
SRT_MAX_DWELL = 7.5


def _audio_duration(path: Path) -> float | None:
    if MediaInfo is None or not path.exists():
        return None
    info = MediaInfo.parse(str(path))
    for track in info.tracks:
        if track.track_type == "Audio" and track.duration:
            return float(track.duration) / 1000.0
    return None


def _parse_srt(path: Path) -> list[dict]:
    """用共用的 SRT 解析器，保证和重映射那一步的理解一致。"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    import srt_util
    return srt_util.parse_srt(path.read_text(encoding="utf-8", errors="ignore"))


def check(plan: dict, base: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []

    width = int(plan.get("canvas", {}).get("width", 1080))
    height = int(plan.get("canvas", {}).get("height", 1920))

    if not plan.get("name"):
        errors.append("缺少 name（剪映里的草稿名）")
    if not plan.get("draft_folder"):
        errors.append("缺少 draft_folder（剪映草稿目录）")
    else:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        import draft_compat
        if not draft_compat.expand_path(plan["draft_folder"]).is_dir():
            errors.append(f"draft_folder 不存在: {plan['draft_folder']}")

    shots = plan.get("shots") or []
    if not shots:
        errors.append("shots 为空")
        return errors, warns

    prev_end = 0.0
    for i, shot in enumerate(shots, 1):
        tag = f"第 {i} 镜"
        src_rel = shot.get("image") or shot.get("video")
        if not src_rel:
            errors.append(f"{tag} 缺少 image / video")
            continue

        src = Path(src_rel) if Path(src_rel).is_absolute() else base / src_rel
        if not src.exists():
            errors.append(f"{tag} 素材不存在: {src}")
            continue

        if "image" in shot:
            try:
                with Image.open(src) as im:
                    w, h = im.size
                if abs(w / h - width / height) > 0.02:
                    warns.append(f"{tag} 比例 {w}x{h} 与画布 {width}x{height} 不一致，可能留黑边或被裁")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{tag} 图片无法读取: {exc}")

        try:
            start, end = float(shot["start"]), float(shot["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{tag} 的 start/end 缺失或非数值")
            continue

        if end <= start:
            errors.append(f"{tag} 时长非正: {start} -> {end}")
        if start < prev_end - 1e-6:
            errors.append(f"{tag} 与上一镜重叠: 上一镜止于 {prev_end:.2f}s，本镜起于 {start:.2f}s")
        elif start > prev_end + 1e-6:
            warns.append(f"{tag} 与上一镜之间有 {start - prev_end:.2f}s 空洞")
        prev_end = max(prev_end, end)

    total = prev_end

    audio_rel = plan.get("audio")
    audio_segs = plan.get("audio_segments") or []
    if audio_rel:
        audio = Path(audio_rel) if Path(audio_rel).is_absolute() else base / audio_rel
        if not audio.exists():
            errors.append(f"音频不存在: {audio}")
        elif audio_segs:
            # 分段音轨：查每段是否对得上画面，以及收尾是否齐
            cursor = 0.0
            for i, a in enumerate(audio_segs, 1):
                a_start, a_end = float(a["start"]), float(a["end"])
                if abs(a_start - cursor) > TOLERANCE:
                    errors.append(
                        f"音频第 {i} 段起于 {a_start:.2f}s，画面接在 {cursor:.2f}s，错位了"
                    )
                if a_end <= a_start:
                    errors.append(f"音频第 {i} 段时长非正")
                cursor = a_end
            if abs(cursor - total) > TOLERANCE:
                errors.append(f"音频止于 {cursor:.2f}s，画面止于 {total:.2f}s，差 {cursor - total:+.2f}s")
            dur = _audio_duration(audio)
            if dur is not None and dur < total - TOLERANCE:
                errors.append(f"音源只有 {dur:.2f}s，撑不到画面结尾 {total:.2f}s")
        else:
            dur = _audio_duration(audio)
            if dur is None:
                warns.append("读不出音频时长（缺少 pymediainfo），跳过音画对齐校验")
            elif abs(dur - total) > TOLERANCE:
                errors.append(f"音画未对齐: 配音 {dur:.2f}s，画面 {total:.2f}s，差 {dur - total:+.2f}s")

    srt_rel = plan.get("srt")
    if srt_rel:
        srt = Path(srt_rel) if Path(srt_rel).is_absolute() else base / srt_rel
        if not srt.exists():
            errors.append(f"字幕不存在: {srt}")
        else:
            cues = _parse_srt(srt)
            if not cues:
                warns.append("字幕文件里没解析出任何合法时间轴，可能格式不对")
            else:
                long_lines = [c for c in cues if len(c["text"]) > SRT_LINE_LIMIT * 2]
                if long_lines:
                    warns.append(
                        f"{len(long_lines)} 条字幕超过 {SRT_LINE_LIMIT * 2} 字，"
                        f"竖屏会顶边（首条：{long_lines[0]['text'][:20]}…）"
                    )
                stuck = [c for c in cues if c["end"] - c["start"] > SRT_MAX_DWELL]
                if stuck:
                    warns.append(f"{len(stuck)} 条字幕停留超过 {SRT_MAX_DWELL}s，看着像卡住")
                over = [c for c in cues if c["end"] > total + 0.5]
                if over:
                    warns.append(f"{len(over)} 条字幕超出画面结尾 {total:.2f}s，会被截断")

    limit = PLATFORM_DURATION.get(str(plan.get("platform") or "").lower())
    if limit and total > limit:
        warns.append(
            f"成片 {total:.0f}s 超过 {plan.get('platform')} 的单条上限 {limit}s，"
            f"要么拆条，要么压到 {limit}s 内"
        )

    return errors, warns


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: check_plan.py <plan.json>", file=sys.stderr)
        return 2

    plan_path = Path(sys.argv[1]).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    errors, warns = check(plan, plan_path.parent)

    for w in warns:
        print(f"[警告] {w}")
    for e in errors:
        print(f"[错误] {e}")

    if errors:
        print(f"\n不通过：{len(errors)} 个错误，先修再导出。")
        return 1

    print(f"\n通过：{len(plan.get('shots', []))} 镜，{len(warns)} 个警告。可以导出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
