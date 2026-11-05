# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""自检：造最小素材 -> 生成一份草稿，验证引擎与剪映目录是否打通。

不依赖 ffmpeg，不调用任何付费接口。跑完打开剪映即可看到名为
"selftest-链路自检" 的草稿。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from draft_builder import build  # noqa: E402

DRAFT_ROOT = Path.home() / "AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft"
WORK = Path(__file__).resolve().parent.parent / "examples" / "selftest"
COLORS = [(28, 32, 48), (46, 34, 30), (24, 44, 40)]
SHOTS = [(0.0, 2.5), (2.5, 5.2), (5.2, 8.0)]
CAPTIONS = ["第一镜：钩子。给一个必须被回答的问题。", "第二镜：推进。给具体信息和数字。", "第三镜：兑现。回答开头那个问题。"]


def _font(size: int):
    for name in ("msyh.ttc", "simhei.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_frames() -> list[str]:
    WORK.mkdir(parents=True, exist_ok=True)
    paths = []
    f_big, f_small = _font(96), _font(40)
    for i, color in enumerate(COLORS, 1):
        img = Image.new("RGB", (1080, 1920), color)
        draw = ImageDraw.Draw(img)
        for y in range(1920):
            shade = int(40 * y / 1920)
            draw.line([(0, y), (1080, y)], fill=tuple(min(255, c + shade) for c in color))
        draw.text((540, 820), f"{i:02d}", font=f_big, fill=(255, 255, 255), anchor="mm")
        draw.text((540, 940), "SELFTEST", font=f_small, fill=(180, 180, 180), anchor="mm")
        p = WORK / f"frame_{i:02d}.png"
        img.save(p)
        paths.append(p.name)
    return paths


def make_srt(path: Path) -> str:
    lines = []
    for i, ((start, end), text) in enumerate(zip(SHOTS, CAPTIONS), 1):
        fmt = lambda s: f"{int(s//3600):02d}:{int(s%3600//60):02d}:{int(s%60):02d},{int(s%1*1000):03d}"  # noqa: E731
        lines += [str(i), f"{fmt(start)} --> {fmt(end)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path.name


def main() -> int:
    frames = make_frames()
    srt = make_srt(WORK / "captions.srt")

    plan = {
        "name": "selftest-链路自检",
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "draft_folder": str(DRAFT_ROOT),
        "srt": srt,
        "shots": [
            {"image": frames[i], "start": s, "end": e, "motion": "push_in"}
            for i, (s, e) in enumerate(SHOTS)
        ],
    }
    plan_path = WORK / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    out = build(plan, WORK)
    print(f"OK 草稿目录: {out}")
    print(f"OK 素材目录: {WORK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
