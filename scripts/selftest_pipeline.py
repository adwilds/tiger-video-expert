# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""端到端自检：造一套假素材，把流水线从头跑到尾。

不依赖真实拍摄素材，也不需要联网下 ASR 模型（转写那步用假 transcript 跳过）。
跑通说明：目录约定、切点、铺画面、草稿写入、兼容层、发布包都对得上。

    python scripts/selftest_pipeline.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

# 60 秒假素材，两机位。内容不重要，只要能解码、有音轨。
FAKE_TRANSCRIPT = {
    "duration": 60.0,
    "segments": [
        {"start": 0.0,  "end": 8.0,  "text": "今天聊一个很多人搞错的事。"},
        {"start": 8.0,  "end": 10.0, "text": "嗯，那个。"},
        {"start": 10.0, "end": 12.0, "text": "等一下，重来一遍。"},
        {"start": 12.0, "end": 25.0, "text": "大部分人以为 AI 剪辑就是自动拼接素材。"},
        {"start": 25.0, "end": 28.0, "text": "不对，说错了。"},
        {"start": 28.0, "end": 44.0, "text": "其实核心在切点。切点决定观众看不看得下去。"},
        {"start": 44.0, "end": 46.0, "text": "嗯。"},
        {"start": 46.0, "end": 60.0, "text": "但是如果你只看工具，就永远做不好这件事。"},
    ],
}

POST = {
    "title": "AI 剪辑真正的门槛不是工具",
    "topic": "AI 剪辑",
    "hook": "大部分人以为 AI 剪辑就是自动拼接素材。不是。",
    "key_points": [
        "切点决定观众看不看得下去，工具决定你多快做完",
        "跳切必须靠换机位或 B-roll 盖住，否则一眼假",
        "先算切点再配画面，顺序反了一定返工",
    ],
    "proof": "这条片子 60 秒素材，剪到 44 秒，跳点 3 处全部用第二机位盖住。",
    "cta": "你剪片子是先配音还是先配画面？评论区说一句。",
    "cover_text": "AI 剪辑的门槛不是工具",
    "hashtags_common": ["AI剪辑", "短视频"],
    "hashtags": {"x": ["AIvideo", "contentcreation"]},
    "titles": {
        "douyin": "AI 剪辑的门槛根本不是工具",
        "xhs": "AI剪辑真正的门槛",
        "shipinhao": "AI剪辑的门槛在切点",
        "x": "The real bottleneck in AI video editing isn't the tool",
    },
    "platforms": ["douyin", "xhs", "shipinhao", "x"],
}


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="ignore")
    return proc


def make_video(ffmpeg: str, dst: Path, color: str, seconds: int = 60) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    run([
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=720x1280:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", str(dst),
    ])


def main() -> int:
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    work = Path(tempfile.mkdtemp(prefix="tiger-selftest-"))
    drafts = work / "drafts"
    proj = work / "proj"
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'✓' if ok else '✗'} {label}" + (f"  {detail}" if detail and not ok else ""))
        if not ok:
            failures.append(label)

    try:
        # --- 造一个"老版本明文布局"的草稿库，模拟真实环境 ---
        legacy = drafts / "existing-draft"
        legacy.mkdir(parents=True)
        (legacy / "draft_content.json").write_text("{}", encoding="utf-8")

        print("\n[1] init")
        p = run([PY, str(HERE / "pipeline.py"), "init", str(proj),
                 "--name", "自检-第01期", "--draft-folder", str(drafts)])
        check("init 退出码 0", p.returncode == 0, p.stderr[-400:])
        check("project.json 生成", (proj / "project.json").exists())
        for d in ("02-raw", "03-transcript", "04-cut", "05-plan", "06-publish"):
            check(f"目录 {d}", (proj / d).is_dir())

        # --- 造双机位素材 ---
        print("\n[2] 造素材")
        make_video(ffmpeg, proj / "02-raw" / "main.mp4", "0x204060")
        make_video(ffmpeg, proj / "02-raw" / "cut.mp4", "0x602040")
        check("main.mp4", (proj / "02-raw" / "main.mp4").stat().st_size > 10000)
        check("cut.mp4", (proj / "02-raw" / "cut.mp4").stat().st_size > 10000)

        # 写 project.json：双机位 + 画布
        project = json.loads((proj / "project.json").read_text(encoding="utf-8"))
        project["angles"] = {"main": "02-raw/main.mp4", "cut": "02-raw/cut.mp4"}
        project["look"] = {"filter": "INS暗", "intensity": 35}
        (proj / "project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

        # --- 跳过 ASR：直接塞假 transcript ---
        print("\n[3] 音轨标准化（真跑 ffmpeg）")
        src_wav = proj / "03-transcript" / "voice_raw.wav"
        p = run([PY, str(HERE / "audio.py"), "standardize",
                 str(proj / "02-raw" / "main.mp4"), str(src_wav)])
        check("standardize 退出码 0", p.returncode == 0, p.stderr[-500:])
        info = {}
        if p.returncode == 0:
            info = json.loads(p.stdout[p.stdout.find("{"):])
            check("响度收敛到 -14±1", abs(info["lufs_after"] - (-14)) < 1.0,
                  f"实际 {info.get('lufs_after')}")
            check("真峰值 <= -1.0", info["true_peak_after"] <= -1.0,
                  f"实际 {info.get('true_peak_after')}")

        (proj / "03-transcript" / "transcript.json").write_text(
            json.dumps(FAKE_TRANSCRIPT, ensure_ascii=False, indent=2), encoding="utf-8")
        (proj / "03-transcript" / "voice.srt").write_text(
            "1\n00:00:00,000 --> 00:00:04,000\n今天聊一个很多人搞错的事。\n\n"
            "2\n00:00:04,000 --> 00:00:09,000\n大部分人以为 AI 剪辑就是自动拼接。\n\n"
            "3\n00:00:09,000 --> 00:00:13,500\n其实核心在切点。\n\n"
            "4\n00:00:13,500 --> 00:00:19,000\n切点决定观众看不看得下去。\n",
            encoding="utf-8")
        # 让 plan 里的音频指向标准化后的文件
        project["audio"] = "03-transcript/voice_std.wav"
        shutil.copy2(src_wav, proj / "03-transcript" / "voice_std.wav")
        (proj / "project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n[4] cut")
        p = run([PY, str(HERE / "pipeline.py"), "cut", str(proj)])
        check("cut 退出码 0", p.returncode == 0, p.stderr[-500:])
        cut_path = proj / "04-cut" / "cutlist.json"
        check("cutlist.json", cut_path.exists())
        check("切点对照表.md", (proj / "04-cut" / "切点对照表.md").exists())
        if cut_path.exists():
            cut = json.loads(cut_path.read_text(encoding="utf-8"))
            removed = [u for u in cut["units"] if not u["keep"]]
            check("删掉了语气词/重录/自我纠正", len(removed) >= 3,
                  f"只删了 {len(removed)} 段")
            kept_text = "".join(u["text"] for u in cut["units"] if u["keep"])
            check("保护词「但是」保留", "但是" in kept_text)
            check("保护词「其实」保留", "其实" in kept_text)
            check("总时长缩短", cut["total_out"] < cut["total_in"],
                  f"{cut['total_out']} vs {cut['total_in']}")

        print("\n[5] build（生成剪映草稿）")
        # 布局由剪映版本决定，先问 doctor 该写哪些文件，再验 build 有没有照着做
        dp = run([PY, str(HERE / "pipeline.py"), "doctor", "--draft-root", str(drafts)])
        layout = ""
        era = ""
        if dp.returncode == 0:
            drep = json.loads(dp.stdout)
            layout = drep.get("草稿库布局") or ""
            era = drep.get("剪映代际") or ""
        check("doctor 给出布局", layout in ("plaintext", "info"), f"实际 {layout}")

        p = run([PY, str(HERE / "pipeline.py"), "build", str(proj)])
        check("build 退出码 0", p.returncode == 0, p.stderr[-800:])
        draft_path = drafts / project["name"]
        check("草稿目录生成", draft_path.exists())
        content_file = draft_path / "draft_content.json"
        if layout == "info":
            info_file = draft_path / "draft_info.json"
            check("新版双写 draft_info.json", info_file.exists())
            if info_file.exists() and content_file.exists():
                check("两个时间轴文件内容一致",
                      info_file.read_bytes() == content_file.read_bytes())
        if content_file.exists():
            check("draft_content.json", True)
            content = json.loads(content_file.read_text(encoding="utf-8"))
            check("画布 1080x1920", content.get("canvas_config", {}).get("width") == 1080
                  or content.get("canvas_config", {}).get("height") == 1920)
            tracks = content.get("tracks") or []
            check("轨道数 >= 3（画面/配音/字幕）", len(tracks) >= 3, f"实际 {len(tracks)}")
            check("有滤镜轨", any(t.get("type") == "filter" for t in tracks),
                  f"轨类型 {[t.get('type') for t in tracks]}")
            meta = json.loads((draft_path / "draft_meta_info.json").read_text(encoding="utf-8"))
            check("draft_meta 路径已修正",
                  str(draft_path.resolve()) in json.dumps(meta.get("draft_fold_path", "")).replace("\\\\", "\\")
                  or meta.get("draft_fold_path", "").startswith(str(draft_path.resolve())[:8]),
                  str(meta.get("draft_fold_path"))[:80])
            check("draft_meta 打上本工具标记（探测时能认出是自己写的）",
                  meta.get("draft_tool") == "tiger-video-expert",
                  str(meta.get("draft_tool")))
        check("plan.json", (proj / "05-plan" / "plan.json").exists())
        check("B-roll待补清单.md", (proj / "05-plan" / "B-roll待补清单.md").exists())

        if (proj / "05-plan" / "plan.json").exists():
            plan = json.loads((proj / "05-plan" / "plan.json").read_text(encoding="utf-8"))
            angles = {s["video"] for s in plan["shots"]}
            check("跳点处切了机位（用上两个角度）", len(angles) >= 2, f"实际 {angles}")

        print("\n[6] verify")
        p = run([PY, str(HERE / "pipeline.py"), "verify", str(proj)])
        check("verify 通过", p.returncode == 0, p.stdout[-600:] + p.stderr[-300:])

        print("\n[7] pack")
        (proj / "06-publish").mkdir(exist_ok=True)
        (proj / "06-publish" / "post.json").write_text(
            json.dumps(POST, ensure_ascii=False, indent=2), encoding="utf-8")
        p = run([PY, str(HERE / "pipeline.py"), "pack", str(proj)])
        check("pack 退出码 0", p.returncode == 0, p.stdout[-600:] + p.stderr[-400:])
        for plat in ("douyin", "xhs", "shipinhao", "x"):
            check(f"{plat}.md", (proj / "06-publish" / f"{plat}.md").exists())
        check("发布前检查.md", (proj / "06-publish" / "发布前检查.md").exists())

        print("\n[8] status")
        p = run([PY, str(HERE / "pipeline.py"), "status", str(proj)])
        check("status 退出码 0", p.returncode == 0)

        print("\n[9] doctor")
        p = run([PY, str(HERE / "pipeline.py"), "doctor", "--draft-root", str(drafts)])
        check("doctor 退出码 0", p.returncode == 0, p.stderr[-400:])
        if p.returncode == 0:
            rep = json.loads(p.stdout)
            expected = "info" if era == "modern" else "plaintext"
            check("布局按剪映版本判定（不靠存量草稿自证）",
                  rep.get("草稿库布局") == expected,
                  f"期望 {expected}，实际 {rep.get('草稿库布局')}")
            check("给出判定依据", bool(rep.get("布局依据")), str(rep.get("布局依据")))
            check("报告剪映版本字段", "剪映版本" in rep, str(list(rep.keys()))[:120])

    finally:
        if failures:
            print(f"\n失败 {len(failures)} 项：{failures}")
            print(f"临时目录保留: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
            print("\n全部通过。")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
