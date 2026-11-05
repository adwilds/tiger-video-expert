# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""视频 -> 逐字转写。

两步：先用 ffmpeg 抽出 16k 单声道音轨（whisper 的要求），再跑 faster-whisper
拿到带时间戳的分段与词级时间，最后落 transcript.json 和 captions.srt。

首次运行会下载模型。国内网络建议先设镜像：
    set HF_ENDPOINT=https://hf-mirror.com

模型越大越准也越慢（CPU 上 small 约实时 1/4，medium 约 1/8）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import imageio_ffmpeg
    from faster_whisper import WhisperModel
except ImportError as exc:  # pragma: no cover
    print(f"缺少依赖：{exc}\n请先 pip install faster-whisper imageio-ffmpeg", file=sys.stderr)
    raise SystemExit(1)


def extract_audio(src: Path, dst: Path) -> Path:
    """抽 16kHz 单声道 wav。whisper 只吃这个规格，先转再喂最稳。"""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-i", str(src),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-800:]
        raise RuntimeError(f"抽音轨失败：{tail}")
    return dst


def _load_f32(audio: Path, sample_rate: int = 16000) -> "np.ndarray":
    """解码成 16k 单声道 float32 PCM 数组。

    不把文件路径直接喂给 whisper：它内部用 PyAV 解码，而 PyAV 的
    `metadata_errors` 参数在不同版本间改过签名，装错版本就直接
    `TypeError` 崩掉。用 ffmpeg 解成裸 PCM 交给 numpy，这条路上
    没有版本敏感的 API，顺带省掉一次重采样。
    """
    import numpy as np

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-v", "error", "-i", str(audio),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "ignore")[-800:]
        raise RuntimeError(f"解码音频失败：{tail}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def transcribe(
    audio: Path,
    model_size: str = "small",
    language: str = "zh",
    device: str = "cpu",
    compute_type: str = "int8",
) -> dict:
    samples = _load_f32(Path(audio))
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        samples,
        language=language,
        word_timestamps=True,
        vad_filter=True,                 # 过滤静音，减少空段
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
    )

    out_segments = []
    for seg in segments:
        out_segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": (seg.text or "").strip(),
            "words": [
                {"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)}
                for w in (seg.words or [])
            ],
        })

    return {
        "language": info.language,
        "language_probability": round(getattr(info, "language_probability", 0.0), 4),
        "duration": round(getattr(info, "duration", 0.0), 3),
        "model": model_size,
        "segments": out_segments,
    }


def _ts(t: float) -> str:
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}"


def to_srt(transcript: dict) -> str:
    blocks = []
    for i, seg in enumerate(transcript["segments"], 1):
        text = seg["text"].strip()
        if not text:
            continue
        blocks.append(f"{i}\n{_ts(seg['start'])} --> {_ts(seg['end'])}\n{text}\n")
    return "\n".join(blocks)


def main() -> int:
    ap = argparse.ArgumentParser(description="视频 -> transcript.json + captions.srt")
    ap.add_argument("source", help="视频或音频文件")
    ap.add_argument("-o", "--out", default=None, help="输出目录（默认与源文件同目录）")
    ap.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium", "large-v3"])
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--keep-audio", action="store_true", help="保留抽出的 wav")
    args = ap.parse_args()

    src = Path(args.source).resolve()
    if not src.exists():
        print(f"源文件不存在：{src}", file=sys.stderr)
        return 1

    out_dir = Path(args.out).resolve() if args.out else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if not os.environ.get("HF_ENDPOINT"):
        print("提示：未设置 HF_ENDPOINT，模型下载走 huggingface.co。国内建议：")
        print("      set HF_ENDPOINT=https://hf-mirror.com\n")

    wav = out_dir / (src.stem + ".16k.wav")
    print(f"[1/2] 抽音轨 -> {wav.name}")
    extract_audio(src, wav)

    print(f"[2/2] 转写中（model={args.model}, device={args.device}）…")
    transcript = transcribe(wav, args.model, args.lang, args.device, args.compute_type)

    tpath = out_dir / "transcript.json"
    tpath.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "captions.srt").write_text(to_srt(transcript), encoding="utf-8")

    if not args.keep_audio:
        wav.unlink(missing_ok=True)

    n = len(transcript["segments"])
    print(f"完成：{n} 段，时长 {transcript['duration']:.1f}s")
    print(f"  {tpath}")
    print(f"  {out_dir / 'captions.srt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
