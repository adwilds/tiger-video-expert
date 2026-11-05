# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""一个入口，跑完整条链。

    # 上游：我是谁 -> 情报 -> 选题
    python pipeline.py profile   init
    python pipeline.py intel     fetch   <项目目录>
    python pipeline.py intel     rank    <项目目录>
    python pipeline.py topic     pick    <项目目录> 1

    # 下游：脚本 -> 预估 -> 素材 -> 粗剪 -> 草稿 -> 发布 -> 复盘
    python pipeline.py init      <项目目录> --name 第01期
    python pipeline.py doctor
    python pipeline.py ingest    <项目目录>
    python pipeline.py cut       <项目目录>
    python pipeline.py build     <项目目录>
    python pipeline.py verify    <项目目录>
    python pipeline.py forecast  <项目目录>
    python pipeline.py pack      <项目目录>
    python pipeline.py metrics   log <项目目录> --platform douyin --views 12000 --completion 42%
    python pipeline.py metrics   report --all .
    python pipeline.py status    <项目目录>

每一步的产出固定在目录里，可以单独重跑，不需要从头再来。
哪一步不通过就停在哪一步，不会带着坏数据往下走。

上游三段的分工要说清：身份和情报是**输入**，选题是**判断**。
脚本只做抓取、去重和可解释的打分排序，判断留给人和专家——机器算不出"反常识"。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import audio as audio_mod          # noqa: E402
import cutlist as cutlist_mod      # noqa: E402
import draft_compat                # noqa: E402

# 项目目录里的固定分区
DIRS = {
    "intel": "00-intel",
    "script": "01-script",
    "raw": "02-raw",
    "transcript": "03-transcript",
    "cut": "04-cut",
    "plan": "05-plan",
    "publish": "06-publish",
    "metrics": "07-metrics",
}

PROJECT_TEMPLATE = {
    "name": "",
    "canvas": {"width": 1080, "height": 1920, "fps": 30},
    "draft_folder": "",
    "angles": {"main": "02-raw/main.mp4"},
    "audio": "03-transcript/voice_std.wav",
    "srt": "03-transcript/voice.srt",
    "look": {"filter": "", "intensity": 60},
    "title_style": {"size": 8.0, "bold": True, "vertical": -0.72},
    "cut": dict(cutlist_mod.DEFAULTS),
    "asr": {"model": "small", "language": "zh"},
    "platforms": ["douyin", "xhs", "shipinhao", "x"],
}


def _project_path(root: Path) -> Path:
    return root / "project.json"


def load_project(root: Path) -> dict:
    p = _project_path(root)
    if not p.exists():
        raise SystemExit(f"找不到 {p}，先跑 pipeline.py init")
    project = json.loads(p.read_text(encoding="utf-8"))
    merged = dict(PROJECT_TEMPLATE)
    merged.update(project)
    merged["cut"] = {**PROJECT_TEMPLATE["cut"], **(project.get("cut") or {})}
    return merged


def save_project(root: Path, project: dict) -> None:
    _project_path(root).write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------- init

def cmd_init(args) -> int:
    root = Path(args.dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for d in DIRS.values():
        (root / d).mkdir(exist_ok=True)

    project = json.loads(json.dumps(PROJECT_TEMPLATE))
    project["name"] = args.name or root.name
    folder = args.draft_folder or draft_compat.guess_draft_root()
    project["draft_folder"] = draft_compat.portable_path(folder) if folder else ""

    save_project(root, project)

    readme = root / "怎么用.md"
    if not readme.exists():
        readme.write_text(_usage_md(project["name"]), encoding="utf-8")

    print(f"项目已建好: {root}")
    print(f"  草稿库: {project['draft_folder'] or '（没自动找到，填 project.json 的 draft_folder）'}")
    if not args.draft_folder and project["draft_folder"]:
        homes = draft_compat.draft_homes()
        if homes:
            print(f"  选它的原因: 这台机器上它装着 {homes[0]['drafts']} 个草稿，是剪映真正在读的库")
    print()

    import profile as profile_mod
    import intel as intel_mod
    ws = intel_mod.workspace_of(root)
    if not (ws / profile_mod.PROFILE_FILE).exists():
        print("还没身份卡。这条链的第一步不是找选题，是说清「我是谁」：")
        print(f"  python scripts/pipeline.py profile init --root {ws}")
        print("  （身份卡是账号级的，放工作区根，不要塞进某一期里）")
        print()
    if not (root / DIRS["intel"] / intel_mod.TOPIC_CARD).exists():
        print("链路：身份 -> 情报 -> 选题 -> 脚本 -> 素材 -> 粗剪 -> 草稿 -> 发布 -> 复盘")
        print("  1. pipeline.py intel fetch .     抓情报")
        print("  2. pipeline.py intel rank .      排候选（只是建议，不是结论）")
        print("  3. pipeline.py topic pick . 1    落成选题卡，然后自己填四栏")
        print()
        print("选题定了再往下走：脚本放 01-script/，素材放 02-raw/main.mp4，然后 pipeline.py ingest .")
    return 0


def _usage_md(name: str) -> str:
    return f"""# {name}

素材放 `02-raw/main.mp4`（有多机位就放 main.mp4 + cut.mp4，并在 project.json 的 angles 里写清）。

然后按顺序跑，每条命令只做一件事，跑完看它最后打的那行：

```bash
python pipeline.py ingest     .     # 转写，出 SRT 和逐词时间
python pipeline.py cut        .     # 算切点，出切点对照表（先看这张表）
python pipeline.py build      .     # 铺画面，生成剪映草稿
python pipeline.py verify     .     # 交付前校验，红了别导出
python pipeline.py pack       .     # 出四个平台的发布稿
```

`python pipeline.py status .` 随时看走到哪一步、下一步该干什么。

## 每一步的产出

| 步骤 | 产物 | 谁看 |
|---|---|---|
| ingest | `03-transcript/voice.srt`、`transcript.json`、`voice_std.wav` | 你不用看 |
| cut | `04-cut/切点对照表.md` | **你要看**，3 分钟 |
| build | `05-plan/plan.json`、`B-roll待补清单.md`、剪映草稿 | 打开剪映看 |
| verify | 终端输出 | 红了就修 |
| pack | `06-publish/<平台>.md`、`发布前检查.md` | **你要看**，然后复制粘贴 |

## 一条要记住的规则

`cut` 之后一定要先看 `切点对照表.md` 再 `build`。
算法只删「明确是废话」的东西，但你的语境它不懂——
看到误删就把它改成 `manual`，重跑 `cut`，算法不会再动它。
"""


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    report: dict = {}

    report["python"] = sys.version.split()[0]
    try:
        import pyJianYingDraft  # noqa: F401
        report["草稿引擎"] = "OK"
    except ImportError:
        report["草稿引擎"] = "缺失：pip install pyJianYingDraft"
    try:
        import imageio_ffmpeg
        report["ffmpeg"] = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        report["ffmpeg"] = "缺失：pip install imageio-ffmpeg"
    try:
        import faster_whisper  # noqa: F401
        report["转写"] = "OK（首次跑会下模型，约 500MB）"
    except ImportError:
        report["转写"] = "缺失：pip install faster-whisper"

    root = Path(args.draft_root) if args.draft_root else draft_compat.guess_draft_root()
    d = draft_compat.doctor(root)
    app = d.get("app") or {}
    report["剪映版本"] = f"{app.get('version') or '未识别'}（{app.get('era')}）"
    report["剪映代际"] = app.get("era")
    report["草稿库"] = d.get("draft_root")
    report["草稿库来源"] = d.get("draft_root_source")
    report["草稿库候选"] = [
        f"{h['drafts']} 个草稿{'（剪映配置指向）' if h.get('configured') else ''} · {h['root']}"
        for h in (d.get("homes") or [])[:3]
    ]
    report["草稿库布局"] = d.get("layout")
    report["布局依据"] = d.get("layout_reason")
    report["草稿库可写"] = d.get("writable")
    report["布局说明"] = d.get("advice")

    print(json.dumps(report, ensure_ascii=False, indent=2))

    bad = [k for k, v in report.items() if isinstance(v, str) and v.startswith("缺失")]
    if bad:
        print(f"\n缺依赖: {', '.join(bad)}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- ingest

def cmd_ingest(args) -> int:
    root = Path(args.dir).resolve()
    project = load_project(root)

    main = project.get("angles", {}).get("main") or project.get("video")
    if not main:
        raise SystemExit("project.json 里没有 angles.main")
    src = Path(main) if Path(main).is_absolute() else root / main
    if not src.exists():
        raise SystemExit(f"主素材不存在: {src}")

    tdir = root / DIRS["transcript"]
    tdir.mkdir(parents=True, exist_ok=True)

    # 1) 抽音轨给 ASR（16k 单声道）
    asr_wav = tdir / "voice_16k.wav"
    print(f"[1/3] 抽音轨 -> {asr_wav.name}")
    audio_mod.extract_mono16k(src, asr_wav)

    # 2) 转写
    import transcribe as tr
    model = (project.get("asr") or {}).get("model", "small")
    lang = (project.get("asr") or {}).get("language", "zh")
    print(f"[2/3] 转写（模型 {model}，语言 {lang}）…首次会下模型")
    transcript = tr.transcribe(asr_wav, model_size=model, language=lang)
    (tdir / "transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (tdir / "voice.srt").write_text(tr.to_srt(transcript), encoding="utf-8")

    # 3) 成品音轨：降噪 + 响度归一
    std_wav = tdir / "voice_std.wav"
    print("[3/3] 音轨标准化（降噪 + -14 LUFS）")
    try:
        res = audio_mod.standardize(src, std_wav)
        print(f"      响度 {res['lufs_before']:.1f} -> {res['lufs_after']:.1f} LUFS")
    except RuntimeError as exc:
        print(f"[警告] 标准化失败，回退用原始音轨: {exc}", file=sys.stderr)
        shutil.copy2(src, std_wav)

    n = len(transcript.get("segments") or [])
    print(f"\n转写完成：{n} 句，{transcript.get('duration', 0):.1f}s")
    if n == 0:
        print(
            "\n[警告] 一句都没转出来。常见原因：\n"
            "  - 素材里没有人声（纯音乐/环境音）\n"
            "  - 音频轨是空的（多音轨素材、或者选了没有声音的那轨）\n"
            "  - 音量太小，VAD 全判成静音\n"
            "先确认素材本身有人声，再重跑。",
            file=sys.stderr,
        )
        return 1
    print("下一步：python pipeline.py cut .")
    return 0


# ---------------------------------------------------------------- cut

def cmd_cut(args) -> int:
    root = Path(args.dir).resolve()
    project = load_project(root)

    tpath = root / DIRS["transcript"] / "transcript.json"
    if not tpath.exists():
        raise SystemExit(f"找不到转写结果 {tpath}，先跑 ingest")
    transcript = json.loads(tpath.read_text(encoding="utf-8"))
    if not (transcript.get("segments") or []):
        raise SystemExit(f"{tpath} 里没有句子。先解决转写为空的问题（pipeline.py ingest 会说明原因）")

    cfg = project.get("cut") or {}
    if args.silence is not None:
        cfg["silence_target"] = args.silence

    result = cutlist_mod.build(transcript, cfg)

    cdir = root / DIRS["cut"]
    cdir.mkdir(parents=True, exist_ok=True)

    payload = {
        "name": project["name"],
        "keep_ranges": [list(r) for r in result.keep_ranges],
        "timeline": result.timeline,
        "warnings": result.warnings,
        "units": [
            {"text": u.text, "start": u.start, "end": u.end,
             "keep": u.keep, "reason": u.reason}
            for u in result.units
        ],
        "total_in": round(result.total_in, 3),
        "total_out": round(result.total_out, 3),
    }
    (cdir / "cutlist.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (cdir / "切点对照表.md").write_text(
        cutlist_mod.to_markdown(result, f"{project['name']} 切点对照表"), encoding="utf-8"
    )

    # 字幕必须跟着重映射，否则剪短之后字幕越说越晚、最后落到画面外
    srt_src = root / DIRS["transcript"] / "voice.srt"
    srt_note = ""
    if srt_src.exists() and result.timeline:
        import srt_util
        text, srt_warns = srt_util.remap_srt(
            srt_src.read_text(encoding="utf-8", errors="ignore"), result.timeline
        )
        (cdir / "voice_cut.srt").write_text(text, encoding="utf-8")
        srt_note = f"字幕已重映射: {cdir / 'voice_cut.srt'}"
        for w in srt_warns:
            print(f"[字幕] {w}")

    kept = sum(1 for u in result.units if u.keep)
    print(f"保留 {kept}/{len(result.units)} 句")
    print(f"时长 {result.total_in:.1f}s -> {result.total_out:.1f}s "
          f"（省 {result.total_in - result.total_out:.1f}s，"
          f"{(1 - result.total_out / result.total_in) * 100 if result.total_in else 0:.0f}%）")
    for w in result.warnings:
        print(f"[告警] {w}")
    if srt_note:
        print(srt_note)
    print(f"\n先看这张表再往下走: {cdir / '切点对照表.md'}")
    print("确认无误后：python pipeline.py build .")
    return 0


# ---------------------------------------------------------------- build

def cmd_build(args) -> int:
    root = Path(args.dir).resolve()
    project = load_project(root)

    cpath = root / DIRS["cut"] / "cutlist.json"
    if not cpath.exists():
        raise SystemExit(f"找不到 {cpath}，先跑 cut")
    cut = json.loads(cpath.read_text(encoding="utf-8"))

    if not project.get("draft_folder"):
        raise SystemExit("project.json 的 draft_folder 是空的，指向剪映草稿库")

    # 优先用重映射后的字幕：源时间轴的字幕直接铺会越说越晚
    remapped = root / DIRS["cut"] / "voice_cut.srt"
    if remapped.exists():
        # 写相对路径：project.json 会进版本库，绝对路径换台机器就废了
        try:
            project["srt"] = remapped.relative_to(root).as_posix()
        except ValueError:
            project["srt"] = str(remapped)
    elif project.get("srt"):
        print("[警告] 没找到重映射字幕，用原始字幕铺，时间可能对不上", file=sys.stderr)

    import draft_builder as db
    plan, report = db.plan_from_cut(cut, project, root)

    pdir = root / DIRS["plan"]
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    db.write_broll_todo(report, pdir / "B-roll待补清单.md", len(plan["shots"]))

    folder = draft_compat.expand_path(project["draft_folder"])
    layout, reason = draft_compat.resolve_layout(getattr(args, "layout", None), folder)
    app = draft_compat.detect_app()
    files = " + ".join(draft_compat.target_filenames(layout))
    print(f"剪映: {app['version'] or '未识别'}（{app['era']}）｜布局: {layout}")
    print(f"  依据: {reason}")
    print(f"  落盘: {files}")

    # 剪映打开并保存过的草稿，时间轴会被改写成加密载荷。这时再生成会整目录重建，
    # 把精剪改动抹掉——除非明确 --force。
    target = folder / plan["name"]
    existing = draft_compat.classify_file(target / draft_compat.CONTENT)
    if existing in ("encrypted", "corrupt") and not getattr(args, "force", False):
        print(
            f"\n拒绝覆盖: {target} 的时间轴已不是明文（{existing}），"
            "说明这个草稿被剪映打开并保存过，里面有你的精剪改动。\n"
            "  要保留：在剪映里「另存为」一份，再改 project.json 的 name\n"
            "  确实要覆盖：加 --force\n",
            file=sys.stderr,
        )
        return 2

    out = db.build(plan, root, layout=layout)

    print(f"跳点 {report['seams']} 处，换机位盖住 {report['angle_switches']} 处，"
          f"还差 {len(report['broll_todo'])} 处 B-roll")
    print(f"草稿已生成: {out}")
    print("下一步：打开剪映确认能读到，然后 python pipeline.py verify .")
    return 0


# ---------------------------------------------------------------- verify

def cmd_verify(args) -> int:
    root = Path(args.dir).resolve()
    ppath = root / DIRS["plan"] / "plan.json"
    if not ppath.exists():
        raise SystemExit(f"找不到 {ppath}，先跑 build")

    import check_plan
    plan = json.loads(ppath.read_text(encoding="utf-8"))
    errors, warns = check_plan.check(plan, root)

    for w in warns:
        print(f"[警告] {w}")
    for e in errors:
        print(f"[错误] {e}")

    if errors:
        print(f"\n不通过：{len(errors)} 个错误，先修再导出。")
        return 1
    print(f"\n通过：{len(plan.get('shots', []))} 镜，{len(warns)} 个警告。可以导出。")
    print("下一步：pipeline.py pack .")
    return 0


# ---------------------------------------------------------------- pack

def cmd_pack(args) -> int:
    root = Path(args.dir).resolve()
    project = load_project(root)

    post = root / DIRS["publish"] / "post.json"
    if not post.exists():
        sample = dict(POST_TEMPLATE)
        sample["platforms"] = project.get("platforms") or POST_TEMPLATE["platforms"]
        post.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"没有 post.json，已生成模板: {post}")
        print("把里面的占位内容换成你的真实内容，再跑一次这条命令。")
        return 2

    core = json.loads(post.read_text(encoding="utf-8"))
    if not core.get("platforms"):
        core["platforms"] = project.get("platforms") or POST_TEMPLATE["platforms"]

    duration = None
    cut_path = root / DIRS["cut"] / "cutlist.json"
    if cut_path.exists():
        duration = json.loads(cut_path.read_text(encoding="utf-8")).get("total_out")

    import pack
    report = pack.build_package(core, root, root / DIRS["publish"], duration)

    print(f"已生成 {len(report['generated'])} 个平台的成稿：{', '.join(report['generated'])}")
    blocked = [n for n, r in report["platforms"].items() if r.get("errors")]
    if blocked:
        print(f"硬性约束不过: {', '.join(blocked)}（看各自的 .md 末尾）")
        return 1
    print(f"检查表: {root / DIRS['publish'] / '发布前检查.md'}")
    return 0


POST_TEMPLATE = {
    "title": "",
    "topic": "",
    "hook": "",
    "key_points": ["", "", ""],
    "proof": "",
    "cta": "",
    "cover_text": "",
    "hashtags_common": [],
    "hashtags": {},
    "titles": {},
    "platforms": ["douyin", "xhs", "shipinhao", "x"],
}


# ---------------------------------------------------------------- 上游三段

def cmd_profile(args) -> int:
    """身份卡：账号级，放工作区根。独立脚本，这里只转发。"""
    import profile as profile_mod
    return profile_mod.main(args.rest)


def cmd_intel(args) -> int:
    """情报：抓取、喂入、打分排序。"""
    import intel as intel_mod
    return intel_mod.main(args.rest)


def cmd_topic(args) -> int:
    """确定选题 = intel pick。单列一个命令，是因为"选题"是链路上的一站，不是子功能。"""
    import intel as intel_mod
    rest = list(args.rest)
    if not rest or rest[0] != "pick":
        # 两种写法都认：topic pick <目录> <序号>、topic <目录> <序号>
        rest = ["pick"] + rest
    return intel_mod.main(rest)


def cmd_forecast(args) -> int:
    """发布前预估：结构体检 + 历史基线外推。不是播放量预测。"""
    import forecast as forecast_mod
    return forecast_mod.main(args.rest)


# ---------------------------------------------------------------- metrics

def cmd_metrics(args) -> int:
    """数据回填是独立脚本，这里只做转发。

    不在 pipeline 里重复实现：metrics 要能跨项目聚合，
    它的「项目目录」参数是可选的复数，和上面每步固定一个目录不一样。
    """
    import metrics as metrics_mod
    return metrics_mod.main(args.rest)


# ---------------------------------------------------------------- board

def cmd_board(args) -> int:
    """只读工作台，转发到 board.py。"""
    import board as board_mod
    argv = ["--root", args.root]
    if args.out:
        argv += ["--out", args.out]
    return board_mod.main(argv)


# ---------------------------------------------------------------- status

def cmd_status(args) -> int:
    root = Path(args.dir).resolve()
    if not _project_path(root).exists():
        print(f"{root} 不是项目目录（没有 project.json）")
        return 1
    project = load_project(root)

    import profile as profile_mod
    import intel as intel_mod
    ws = intel_mod.workspace_of(root)

    def show(p: Path) -> str:
        try:
            return str(p.relative_to(root))
        except ValueError:
            return str(p)

    # (标签, 路径, 该跑什么, 是否卡住流程)
    steps = [
        ("身份", ws / profile_mod.PROFILE_FILE, "pipeline.py profile init", True),
        ("情报", ws / intel_mod.INTEL_DIR / intel_mod.RAW_FILE, "pipeline.py intel fetch .", True),
        ("选题", root / DIRS["intel"] / intel_mod.TOPIC_CARD, "pipeline.py topic pick . 1", True),
        ("脚本", root / DIRS["script"] / "script.md", "写脚本放进来", False),
        ("预估", root / DIRS["publish"] / "预估.md", "pipeline.py forecast .", False),
        ("素材", root / DIRS["raw"] / "main.mp4", "把主素材放进来", True),
        ("转写", root / DIRS["transcript"] / "transcript.json", "pipeline.py ingest .", True),
        ("切点", root / DIRS["cut"] / "cutlist.json", "pipeline.py cut .", True),
        ("草稿", root / DIRS["plan"] / "plan.json", "pipeline.py build .", True),
        ("发布稿", root / DIRS["publish"] / "post.json", "pipeline.py pack .", True),
        ("数据", root / DIRS["metrics"] / "metrics.csv",
         "pipeline.py metrics log . --platform douyin --views ... --completion ...", True),
    ]

    print(f"项目: {project['name']}")
    print(f"草稿库: {project.get('draft_folder') or '（未设置）'}")
    topic = project.get("topic")
    if topic:
        print(f"选题: {topic.get('title', '')}（{topic.get('source', '')}）")
    print()
    nxt = None
    for label, path, action, required in steps:
        ok = path.exists()
        mark = "✓" if ok else ("·" if not required else "○")
        tail = "" if required else "  （可选）"
        print(f"  {mark} {label:<4} {show(path)}{tail}")
        if not ok and nxt is None and required:
            nxt = (label, action)

    print()
    if nxt:
        print(f"下一步：{nxt[1]}")
    else:
        print("全流程产物齐了。打开剪映精剪，然后按 06-publish 里的稿子发。")
    return 0


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="全链路内容流水线：身份 -> 情报 -> 选题 -> 脚本 -> 预估 -> 素材 -> 粗剪 -> 草稿 -> 发布稿 -> 复盘",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("profile", help="身份卡：我是谁、说给谁听")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("intel", help="情报采集与选题排序")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_intel)

    p = sub.add_parser("topic", help="确定选题（= intel pick）")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_topic)

    p = sub.add_parser("init", help="建项目骨架")
    p.add_argument("dir")
    p.add_argument("--name", default="")
    p.add_argument("--draft-folder", default="")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="环境与草稿库自检")
    p.add_argument("--draft-root", default="")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("ingest", help="转写 + 音轨标准化")
    p.add_argument("dir")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("cut", help="算切点")
    p.add_argument("dir")
    p.add_argument("--silence", type=float, default=None, help="停顿压缩目标秒数")
    p.set_defaults(func=cmd_cut)

    p = sub.add_parser("build", help="生成剪映草稿")
    p.add_argument("dir")
    p.add_argument("--layout", default="auto",
                   choices=["auto", "plaintext", "content", "info", "both"],
                   help="时间轴文件写哪个；auto 按剪映版本判，升级后判错就手动指定")
    p.add_argument("--force", action="store_true",
                   help="草稿已被剪映保存过（时间轴变加密）时仍然覆盖，会丢掉精剪改动")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="交付前校验")
    p.add_argument("dir")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("pack", help="生成多平台发布稿")
    p.add_argument("dir")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("forecast", help="发布前预估：结构体检 + 历史基线外推")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_forecast)

    p = sub.add_parser("metrics", help="数据回填与迭代")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("board", help="生成只读工作台（HTML）")
    p.add_argument("--root", default=".", help="扫描根目录")
    p.add_argument("--out", default="", help="输出文件名")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("status", help="看走到哪一步")
    p.add_argument("dir")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
