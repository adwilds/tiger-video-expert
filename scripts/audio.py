# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""配音标准化：底噪、低频、响度。

三件事，顺序不能反：

1. **高通** 80Hz —— 先切掉空调声、桌面震动、喷麦低频。放在降噪之前，
   否则降噪会把低频噪声当成信号保留下来。
2. **降噪** afftdn —— 减稳态底噪（电流声、风扇声），不碰人声主体。
3. **响度归一** loudnorm 两遍 —— 目标是各平台的投放标准：
   抖音/视频号 -14 LUFS，X/YouTube -14，播客 -16。统一到 -14 更保险，
   因为平台只会往下压，不会往上补。

为什么必须两遍：一遍 loudnorm 是动态压缩，会把人声的起伏一起压平，
听感发闷。两遍（先测量再静态施加）才能做到「响度达标但动态还在」。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

TARGET_LUFS = -14.0
TARGET_TP = -1.5       # 真峰值上限，留 1.5dB 给平台二次编码
TARGET_LRA = 11.0      # 响度范围，口播留 11 足够
HIGHPASS_HZ = 80


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 imageio-ffmpeg。安装：pip install imageio-ffmpeg"
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-nostdin", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1200:]
        raise RuntimeError(f"ffmpeg 失败（{proc.returncode}）:\n{tail}")
    return proc


def duration(path: Path) -> float | None:
    """读时长（秒）。不依赖 ffprobe，用 ffmpeg -i 的输出解析。"""
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr or "")
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def extract_mono16k(src: Path, dst: Path) -> Path:
    """抽 16k 单声道 wav，给 whisper 用。whisper 内部也会重采样，
    但先抽好能省一半解码时间，VAD 也更准。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(dst)])
    return dst


def _measure(src: Path, prefilter: str) -> dict:
    proc = run([
        "-i", str(src), "-vn",
        "-af", f"{prefilter}loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
        "-f", "null", "-",
    ])
    blob = proc.stderr
    start = blob.rfind("{")
    end = blob.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("loudnorm 没有返回测量结果")
    return json.loads(blob[start:end + 1])


def standardize(
    src: Path,
    dst: Path,
    *,
    target_lufs: float = TARGET_LUFS,
    denoise: bool = True,
    highpass: bool = True,
    sample_rate: int = 48000,
) -> dict:
    """把一段配音做成可直接进剪辑的成品音轨。

    返回测量前后的响度，用来给用户一个「改了没有、改了多少」的交代。
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise FileNotFoundError(f"音频不存在: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    chain = []
    if highpass:
        chain.append(f"highpass=f={HIGHPASS_HZ}")
    if denoise:
        chain.append("afftdn=nf=-25:tn=1")
    prefilter = ",".join(chain) + ("," if chain else "")

    before = _measure(src, prefilter)

    af = (
        f"{prefilter}loudnorm=I={target_lufs}:TP={TARGET_TP}:LRA={TARGET_LRA}"
        f":measured_I={before['input_i']}"
        f":measured_TP={before['input_tp']}"
        f":measured_LRA={before['input_lra']}"
        f":measured_thresh={before['input_thresh']}"
        f":offset={before['target_offset']}"
        ":linear=true:print_format=summary"
    )

    run([
        "-y", "-i", str(src), "-vn", "-af", af,
        "-ar", str(sample_rate), "-ac", "2",
        "-c:a", "pcm_s16le", str(dst),
    ])

    after = _measure(dst, "")

    return {
        "src": str(src),
        "dst": str(dst),
        "lufs_before": float(before["input_i"]),
        "lufs_after": float(after["input_i"]),
        "target_lufs": target_lufs,
        "true_peak_after": float(after["input_tp"]),
        "duration": duration(dst),
    }


def mix_bgm(voice: Path, bgm: Path, dst: Path, *, bgm_db: float = -22.0,
            duck: bool = True) -> Path:
    """人声 + BGM 混一轨。duck 打开时 BGM 会在人声出现时自动让位。

    这是给「不想在剪映里手调音量包络」的人用的兜底：先在 ffmpeg 里混平，
    进剪映后 BGM 只需要整体微调，不用逐句压。
    """
    voice, bgm, dst = Path(voice), Path(bgm), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = duration(voice)
    if not total:
        raise RuntimeError(f"读不出人声时长: {voice}")

    if duck:
        # sidechaincompress: 人声当触发源，BGM 当被压对象
        filt = (
            f"[1:a]volume={bgm_db}dB,aloop=loop=-1:size=2e9[bg];"
            f"[0:a]asplit=2[v1][v2];"
            f"[bg][v2]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=350[bgd];"
            f"[v1][bgd]amix=inputs=2:duration=first:dropout_transition=0[out]"
        )
    else:
        filt = (
            f"[1:a]volume={bgm_db}dB,aloop=loop=-1:size=2e9[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[out]"
        )

    run(["-y", "-i", str(voice), "-stream_loop", "-1", "-i", str(bgm),
         "-filter_complex", filt, "-map", "[out]",
         "-t", f"{total:.3f}", "-ar", "48000", "-ac", "2",
         "-c:a", "pcm_s16le", str(dst)])
    return dst


def _main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "用法:\n"
            "  audio.py standardize <输入> <输出> [--lufs -14] [--no-denoise]\n"
            "  audio.py mono16k <输入> <输出>\n"
            "  audio.py mix <人声> <BGM> <输出> [--bgm-db -22] [--no-duck]\n"
            "  audio.py info <音频>",
            file=sys.stderr,
        )
        return 2

    cmd, args = argv[1], argv[2:]
    flags = {a for a in args if a.startswith("--")}
    pos = [a for a in args if not a.startswith("--")]
    value_of = {}
    for i, a in enumerate(args):
        if a.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            value_of[a] = args[i + 1]
            if args[i + 1] in pos:
                pos.remove(args[i + 1])

    if cmd == "info":
        d = duration(Path(pos[0]))
        print(json.dumps({"file": pos[0], "duration": d}, ensure_ascii=False))
        return 0

    if cmd == "mono16k":
        print(extract_mono16k(Path(pos[0]), Path(pos[1])))
        return 0

    if cmd == "standardize":
        lufs = float(value_of.get("--lufs", TARGET_LUFS))
        res = standardize(
            Path(pos[0]), Path(pos[1]),
            target_lufs=lufs,
            denoise="--no-denoise" not in flags,
            highpass="--no-highpass" not in flags,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if cmd == "mix":
        out = mix_bgm(
            Path(pos[0]), Path(pos[1]), Path(pos[2]),
            bgm_db=float(value_of.get("--bgm-db", -22.0)),
            duck="--no-duck" not in flags,
        )
        print(out)
        return 0

    print(f"未知命令: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
