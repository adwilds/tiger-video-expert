# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""plan.json -> 剪映草稿

编排层：本文件只负责把中间格式翻译成草稿操作，
草稿数据结构本身由 pyJianYingDraft（Apache-2.0）负责，不重复实现。

两条入口：
    plan_from_cut(...)  从切点结果直接铺一条粗剪时间线（最常用）
    build(...)          按 plan.json 落草稿

跳切掩盖：源素材里每剪掉一段，画面上就有一个跳点。人眼看着是「人突然
窜了一下」，很廉价。专业做法是在跳点处切机位——观众以为你换了角度，
其实你只是剪掉了废话。所以 plan_from_cut 会在每个断开处自动换机位，
没有第二机位时把它列进 B-roll 待补清单，而不是硬接。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyJianYingDraft as draft
from pyJianYingDraft import ClipSettings, KeyframeProperty, TextStyle, TrackSpec, TrackType

try:
    from . import draft_compat
except ImportError:  # 直接当脚本跑
    sys.path.insert(0, str(Path(__file__).parent))
    import draft_compat  # type: ignore

# 缓推幅度：整段从 1.0 推到 1 + MOTION_ZOOM，避免静帧呆板
MOTION_ZOOM = {"push_in": 0.10, "pull_out": -0.08, "none": 0.0}

# 拼接缝隙判定：小于这个值当作连续，不换机位
SEAM_EPS = 0.05

# 转场名 -> 引擎枚举。写错名字不静默忽略，直接报错，避免"以为加了其实没加"
TRANSITIONS = {
    "叠化": "叠化",
    "淡入淡出": "淡入淡出",
    "闪白": "闪白",
    "闪黑": "闪黑",
    "左推": "左移",
    "右推": "右移",
    "上推": "上移",
    "下推": "下移",
    "推近": "推近",
    "拉远": "拉远",
    "模糊": "模糊",
    "无": None,
    "": None,
}


def _resolve(base: Path, rel: str) -> str:
    p = Path(rel)
    if not p.is_absolute():
        p = base / p
    if not p.exists():
        raise FileNotFoundError(f"素材不存在: {p}")
    return str(p)


def _transition_of(name: str):
    if name not in TRANSITIONS:
        raise ValueError(
            f"未知转场「{name}」，可用: {', '.join(k for k in TRANSITIONS if k)}"
        )
    key = TRANSITIONS[name]
    if key is None:
        return None
    return getattr(draft.TransitionType, key)


def _intro_of(name: str):
    if not name:
        return None
    if not hasattr(draft.IntroType, name):
        raise ValueError(f"未知入场动画「{name}」")
    return getattr(draft.IntroType, name)


# ---------------------------------------------------------------- 从切点铺时间线

def plan_from_cut(cut: dict, project: dict, base: Path) -> tuple[dict, dict]:
    """把 cutlist.json 的 timeline 变成本项目要铺的画面。

    返回 (plan, report)：plan 可直接交给 build()；report 记录换机位与待补 B-roll。

    两件必须做对的事：

    **画面不能有空隙。** 切点表按「保留区间 + 停顿」建模，划出来的新时间轴
    中间是留白的——那是给听觉留的呼吸，但铺到视频轨上就是黑屏。所以这里
    把留白折算成源素材：能往前多吃就往前吃（人说话前后的静默本来就在源里），
    吃不下的就收掉，保证画面首尾相接。

    **配音要跟着切。** 原音轨是 60 秒，剪完是 45 秒，直接挂整条音轨就错位。
    所以音频也按同一套保留区间切段，一段对一段。
    """
    angles = project.get("angles") or {}
    main_angle = angles.get("main") or project.get("video")
    if not main_angle:
        raise ValueError("project.json 里缺 angles.main（或 video），不知道拿什么铺画面")
    alt_angle = angles.get("cut") or angles.get("alt")

    timeline = cut.get("timeline") or []
    if not timeline:
        raise ValueError("cutlist.json 里没有 timeline，先跑切点")

    audio_src = project.get("audio")

    shots: list[dict] = []
    audio_segments: list[dict] = []
    report = {"seams": 0, "angle_switches": 0, "broll_todo": [],
              "pause_absorbed": 0.0, "pause_dropped": 0.0}

    cursor = 0.0
    prev_src_end: float | None = None
    prev_dst_end: float | None = None
    use_alt = False

    for seg in timeline:
        src_start = float(seg["src_start"])
        src_end = float(seg["src_end"])
        dst_start = float(seg["dst_start"])

        # 把上一段之后留的停顿折算回源区间
        if prev_dst_end is not None:
            gap = dst_start - prev_dst_end
            if gap > 1e-6 and prev_src_end is not None:
                room = max(0.0, src_start - prev_src_end)
                take = min(gap, room)
                if take > 1e-6:
                    src_start -= take
                    report["pause_absorbed"] += take
                rest = gap - take
                if rest > 1e-6:
                    report["pause_dropped"] += rest

        dur = src_end - src_start
        if dur <= 0:
            prev_src_end, prev_dst_end = src_end, dst_start
            continue

        seam = prev_src_end is not None and (src_start - prev_src_end) > SEAM_EPS
        if seam:
            report["seams"] += 1
            use_alt = not use_alt

        angle = main_angle
        if use_alt and alt_angle:
            angle = alt_angle
            report["angle_switches"] += 1
        elif use_alt and not alt_angle:
            report["broll_todo"].append({
                "dst_start": round(cursor, 2),
                "src_start": round(src_start, 2),
                "why": "上一段在这里断开，画面会跳；补一个 B-roll 或切机位盖住",
            })
            use_alt = False  # 没机位可切，别把标志留着

        shots.append({
            "start": round(cursor, 3),
            "end": round(cursor + dur, 3),
            "video": angle,
            "src_start": round(src_start, 3),
            "motion": "none",
        })
        if audio_src:
            audio_segments.append({
                "start": round(cursor, 3),
                "end": round(cursor + dur, 3),
                "src_start": round(src_start, 3),
            })

        cursor += dur
        prev_src_end = src_end
        prev_dst_end = float(seg["dst_end"])

    plan = {
        "name": project.get("name") or cut.get("name") or "粗剪",
        "draft_folder": project["draft_folder"],
        "canvas": project.get("canvas", {"width": 1080, "height": 1920, "fps": 30}),
        "look": project.get("look") or {},
        "title_style": project.get("title_style") or {},
        "shots": shots,
    }
    if audio_src:
        plan["audio"] = audio_src
        plan["audio_segments"] = audio_segments
    if project.get("srt"):
        plan["srt"] = project["srt"]

    return plan, report


def write_broll_todo(report: dict, out_path: Path, total_keep: int) -> None:
    lines = [
        "# B-roll 待补清单",
        "",
        f"保留片段 {total_keep} 段，跳点 {report['seams']} 处，"
        f"已用换机位盖住 {report['angle_switches']} 处，还差 {len(report['broll_todo'])} 处。",
        "",
    ]
    if not report["broll_todo"]:
        lines += ["全部跳点都盖住了，不需要补 B-roll。"]
    else:
        lines += [
            "下面这些时间点的画面会「窜」，补一段空镜（手部动作、屏幕、白板、街景）",
            "盖住 ≤1.5 秒即可。补法是回到剪映，在第 5 轨（贴纸/画中画）铺素材，",
            "或者直接把 B-roll 丢给流水线重跑一次。",
            "",
            "| 成片时间点 | 对应源素材时间 | 说明 |",
            "|---|---|---|",
        ]
        for item in report["broll_todo"]:
            lines.append(
                f"| {item['dst_start']:.2f}s | {item['src_start']:.2f}s | {item['why']} |"
            )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 落草稿

def _text_style(cfg: dict) -> TextStyle:
    return TextStyle(
        size=float(cfg.get("size", 8.0)),
        bold=bool(cfg.get("bold", True)),
        color=tuple(cfg.get("color", (1.0, 1.0, 1.0))),
        align=int(cfg.get("align", 1)),
        auto_wrapping=True,
        max_line_width=float(cfg.get("max_line_width", 0.82)),
    )


def _text_clip(cfg: dict) -> ClipSettings:
    # 竖屏字幕默认压在画面下方 72% 处，避开人脸和平台 UI 遮挡区
    return ClipSettings(
        transform_y=float(cfg.get("vertical", -0.72)),
        scale_x=1.0,
        scale_y=1.0,
    )


def build(plan: dict, plan_dir: Path, *, layout: str = "plaintext") -> Path:
    spec = plan.get("canvas", {})
    width = int(spec.get("width", 1080))
    height = int(spec.get("height", 1920))
    fps = int(spec.get("fps", 30))

    folder_path = str(draft_compat.expand_path(plan["draft_folder"]))
    folder = draft.DraftFolder(folder_path)
    script = folder.create_draft(plan["name"], width, height, fps, allow_replace=True)

    video_track = script.append_track(TrackSpec(TrackType.video, name="画面"))
    audio_track = script.append_track(TrackSpec(TrackType.audio, name="配音"))

    shots = plan.get("shots", [])
    if not shots:
        raise ValueError("plan.shots 为空，没有可铺的画面")

    prev_end = 0.0
    segments: list = []
    pending_intro: dict | None = None

    for idx, shot in enumerate(shots, 1):
        start = float(shot["start"])
        end = float(shot["end"])
        if end <= start:
            raise ValueError(f"第 {idx} 镜时长非正: {start} -> {end}")
        if start < prev_end - 1e-6:
            raise ValueError(f"第 {idx} 镜与上一镜重叠: {prev_end} -> {start}")
        prev_end = end

        src = shot.get("image") or shot.get("video")
        if not src:
            raise ValueError(f"第 {idx} 镜既没有 image 也没有 video")
        material = _resolve(plan_dir, src)

        source_range = None
        if "src_start" in shot:
            src_start = float(shot["src_start"])
            source_range = draft.trange_seconds(src_start, end=src_start + (end - start))

        seg = draft.VideoSegment(
            material,
            draft.trange_seconds(start, end=end),
            source_timerange=source_range,
            clip_settings=ClipSettings(scale_x=1.0, scale_y=1.0),
        )

        # 缓推只对静帧有意义；视频段推近会吃掉画面边缘，默认不加
        zoom = MOTION_ZOOM.get(shot.get("motion", "none"), 0.0)
        if zoom and "image" in shot:
            base = 1.0 + zoom
            seg.add_keyframe(KeyframeProperty.uniform_scale, "0s", base)
            seg.add_keyframe(KeyframeProperty.uniform_scale, f"{end - start:.3f}s", 1.0 + zoom * 2)

        # 转场挂在「上一段」的尾部，表示这一段与上一段之间的过渡
        t_name = shot.get("transition")
        if t_name and segments:
            t_type = _transition_of(t_name)
            if t_type is not None:
                dur = float(shot.get("transition_duration", 0.4))
                segments[-1].add_transition(t_type, duration=f"{dur:.2f}s")

        intro = _intro_of(shot.get("anim") or "")
        if intro is not None:
            dur = shot.get("anim_duration")
            seg.add_animation(intro, duration=dur)

        script.add_segment(seg, video_track)
        segments.append(seg)

    total = prev_end

    audio_rel = plan.get("audio")
    if audio_rel:
        audio_path = _resolve(plan_dir, audio_rel)
        segs = plan.get("audio_segments") or []
        if segs:
            # 按保留区间切段：音画逐段对齐，粗剪后不会有累计漂移
            for a in segs:
                a_start, a_end = float(a["start"]), float(a["end"])
                a_src = float(a.get("src_start", a_start))
                script.add_segment(
                    draft.AudioSegment(
                        audio_path,
                        draft.trange_seconds(a_start, end=a_end),
                        source_timerange=draft.trange_seconds(
                            a_src, end=a_src + (a_end - a_start)
                        ),
                        volume=float(plan.get("volume", 1.0)),
                    ),
                    audio_track,
                )
        else:
            script.add_segment(
                draft.AudioSegment(audio_path, draft.trange_seconds(0, end=total)),
                audio_track,
            )

    look = plan.get("look") or {}
    filter_name = look.get("filter")
    if filter_name:
        if not hasattr(draft.FilterType, filter_name):
            raise ValueError(f"未知滤镜「{filter_name}」")
        script.append_track(TrackSpec(TrackType.filter, name="调色"))
        script.add_filter(
            getattr(draft.FilterType, filter_name),
            draft.trange_seconds(0, end=total),
            track_name="调色",
            intensity=float(look.get("intensity", 60.0)),
        )

    srt_rel = plan.get("srt")
    if srt_rel:
        srt_path = _resolve(plan_dir, srt_rel)
        script.append_track(TrackSpec(TrackType.text, name="字幕"))
        style_cfg = plan.get("title_style") or {}
        script.import_srt(
            srt_path,
            track_name="字幕",
            text_style=_text_style(style_cfg),
            clip_settings=_text_clip(style_cfg),
        )

    script.save()

    draft_path = Path(folder_path) / plan["name"]
    # 先修时间轴自身的 id 和时长，再复制成 draft_info.json，否则 info 里是旧值
    content_fix = draft_compat.fix_draft_content(draft_path)
    if not content_fix.get("ok"):
        print(f"[警告] 时间轴 id/时长没对齐: {content_fix.get('reason')}", file=sys.stderr)
    draft_compat.apply_layout(draft_path, layout)
    meta = draft_compat.fix_draft_meta(draft_path)
    if not meta.get("ok"):
        print(f"[警告] 草稿元信息没修好: {meta.get('reason')}", file=sys.stderr)

    return draft_path


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: draft_builder.py <plan.json>", file=sys.stderr)
        return 2
    plan_path = Path(sys.argv[1]).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    layout = plan.get("layout") or draft_compat.inspect(draft_compat.expand_path(plan["draft_folder"])).get("layout", "plaintext")
    out = build(plan, plan_path.parent, layout=layout)
    print(f"草稿已生成: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
