# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""剪映草稿库兼容层。

为什么需要这一层：

剪映 6.0 起把 draft_content.json 改成 AES 加密的二进制，同时在部分版本上
改用 draft_info.json 作为主时间轴文件。用旧的写法（只写 draft_content.json）
在新版剪映里打开会报「草稿内容已损坏」——草稿生成了，但打不开。

这一层不碰加密（不逆向、不解密），只做两件事：

1. **探测**：这个草稿库里，现存的草稿用的是哪种布局，素材能不能读。
2. **对齐**：生成新草稿时，按探测到的布局决定写哪个文件名。

探测结果三选一：
    plaintext   明文 draft_content.json —— 老版本剪映，照旧写
    info        明文 draft_info.json    —— 新版剪映，写 draft_info.json
    encrypted   加密载荷               —— 6.0+ 已加密，仍写明文 draft_info.json
                                          （剪映打开时会原地升级并加密）
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

# 剪映 Windows / macOS 默认草稿库位置，按优先级排列
DEFAULT_HINTS = [
    r"%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft",
    r"%LOCALAPPDATA%\JianyingPro\User Data\Projects",
    "~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
    "~/Library/Application Support/JianyingPro/User Data/Projects/com.lveditor.draft",
]

CONTENT = "draft_content.json"
INFO = "draft_info.json"


def expand_path(p: str | Path) -> Path:
    """展开 %VAR% 和 ~。

    project.json / plan.json 里允许写 `%LOCALAPPDATA%\\JianyingPro\\...`，
    换台机器只要剪映装在默认位置就不用改配置。
    """
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


def portable_path(p: str | Path) -> str:
    """反过来：指向本机 %LOCALAPPDATA% 的路径写成变量形式，别把用户名写进文件。"""
    s = str(p)
    local = os.environ.get("LOCALAPPDATA")
    if local and s.lower().startswith(local.lower()):
        return "%LOCALAPPDATA%" + s[len(local):]
    return s


def _expand(p: str) -> Path:
    return expand_path(p)


def guess_draft_root() -> Path | None:
    """在常见位置里找第一个存在的剪映草稿库。找不到返回 None。"""
    for hint in DEFAULT_HINTS:
        p = _expand(hint)
        if p.is_dir():
            return p
    return None


def classify_file(path: Path) -> str:
    """判断单个草稿文件的形态：plaintext / encrypted / missing / corrupt。"""
    if not path.exists():
        return "missing"
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return "corrupt"
    stripped = head.lstrip()
    if not stripped.startswith(b"{"):
        return "encrypted"
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "corrupt"
    return "plaintext"


def inspect(draft_root: Path, limit: int = 200) -> dict:
    """统计草稿库布局。

    返回:
        {
          "root": str,
          "total": int,             # 子目录数
          "sampled": int,           # 实际检查过的草稿数
          "layout": "plaintext"|"info"|"encrypted"|"empty"|"mixed",
          "counts": {"plaintext": n, "encrypted": n, "info-only": n, ...},
          "examples": [{"draft": name, "content": "plaintext", "info": "missing"}],
          "advice": str,
        }
    """
    root = Path(draft_root)
    if not root.is_dir():
        return {
            "root": str(root),
            "error": "草稿库目录不存在",
            "layout": "missing",
            "advice": "在剪映里随便新建一个草稿，再把 project.json 的 draft_folder 指到它所在的目录。",
        }

    subs = [p for p in sorted(root.iterdir())
            if p.is_dir() and not p.name.startswith(".")][:limit]
    counts = {"plaintext": 0, "encrypted": 0, "corrupt": 0, "missing": 0, "info-only": 0}
    examples: list[dict] = []

    for sub in subs:
        c = classify_file(sub / CONTENT)
        i = classify_file(sub / INFO)
        shape = {"draft": sub.name, "content": c, "info": i}
        examples.append(shape)

        if i == "plaintext" and c == "missing":
            counts["info-only"] += 1
        elif c in counts:
            counts[c] += 1

    if not subs:
        layout = "empty"
    elif counts["info-only"] > 0 and counts["plaintext"] == 0:
        layout = "info"
    elif counts["plaintext"] > 0 and counts["encrypted"] == 0:
        layout = "plaintext"
    elif counts["encrypted"] > 0 and counts["info-only"] > 0:
        layout = "info"          # 新版 + 加密并存，按 info 布局写
    elif counts["encrypted"] > 0 and counts["plaintext"] == 0:
        layout = "encrypted"
    else:
        layout = "mixed"

    return {
        "root": str(root),
        "total": len(subs),
        "sampled": len(subs),
        "layout": layout,
        "counts": counts,
        "examples": examples[:5],
        "advice": _advice(layout, counts),
    }


def _advice(layout: str, counts: dict) -> str:
    if layout == "empty":
        return "草稿库是空的。先写一个草稿，打开剪映确认能读到，再按提示定布局。"
    if layout == "plaintext":
        return "老版本布局（明文 draft_content.json），照旧写即可。"
    if layout == "info":
        return "新版布局（draft_info.json 为主）。生成草稿时会同时写 draft_info.json。"
    if layout == "encrypted":
        return (
            "草稿库已加密（剪映 6.0+）。不要试图解密：改为写明文 draft_info.json，"
            "剪映打开时会原地读取并升级加密。若仍报「草稿内容已损坏」，"
            "把剪映降到 5.9.x 并关掉自动更新，或改用 CapCut 国际版。"
        )
    return "布局混杂（新旧草稿并存）。默认按 draft_info.json 写，并保留 draft_content.json 副本。"


def target_filenames(layout: str) -> list[str]:
    """按布局返回该写哪些文件名。"""
    if layout == "plaintext":
        return [CONTENT]
    # info / encrypted / mixed / empty 一律双写：
    # 老版本读 content，新版本读 info，两边都不落空。
    return [CONTENT, INFO]


def apply_layout(draft_path: Path, layout: str) -> Path:
    """草稿生成后按布局补齐文件名。返回主时间轴文件路径。

    pyJianYingDraft 只写 draft_content.json。这里在需要时把它复制成
    draft_info.json，让新版剪映也能读到。
    """
    draft_path = Path(draft_path)
    content = draft_path / CONTENT
    if not content.exists():
        raise FileNotFoundError(f"草稿内容缺失: {content}")

    wanted = target_filenames(layout)
    for name in wanted:
        dst = draft_path / name
        if dst == content or dst.exists():
            continue
        shutil.copy2(content, dst)

    return content


def fix_draft_meta(draft_path: Path) -> dict:
    """把 draft_meta_info.json 里的路径字段改成实际路径。

    pyJianYingDraft 用的是模板，里面的 draft_fold_path 等字段是占位值。
    不改的话剪映列表里能看到草稿，但缩略图和打开都可能是空的。
    """
    draft_path = Path(draft_path).resolve()
    meta_file = draft_path / "draft_meta_info.json"
    if not meta_file.exists():
        return {"ok": False, "reason": "缺 draft_meta_info.json"}

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"draft_meta_info.json 解析失败: {exc}"}

    changed = 0
    for key in ("draft_fold_path", "draft_root_path", "draft_timeline_materials_size_",
                "draft_removable_storage_device", "draft_materials_path"):
        if key in meta:
            meta[key] = str(draft_path)
            changed += 1

    # 草稿名 / 文件名对齐
    if "draft_name" in meta:
        meta["draft_name"] = draft_path.name
    if "draft_id" in meta and not meta["draft_id"]:
        meta["draft_id"] = draft_path.name

    meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "fields_fixed": changed}


def doctor(draft_root: Path | None = None) -> dict:
    """环境自检：草稿库在哪、能不能写、布局是什么。"""
    root = Path(draft_root) if draft_root else guess_draft_root()
    report: dict = {"draft_root": str(root) if root else None}

    if root is None:
        report["ok"] = False
        report["advice"] = (
            "没找到剪映草稿库。打开剪映随便新建一个草稿，"
            "然后把它的路径填进 project.json 的 draft_folder。"
        )
        return report

    report["ok"] = True
    report["writable"] = os.access(root, os.W_OK) if root.is_dir() else False
    report.update(inspect(root))
    return report


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(doctor(target), ensure_ascii=False, indent=2))
