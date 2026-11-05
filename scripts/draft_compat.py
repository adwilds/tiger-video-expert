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
MARK = "tiger-video-expert"

# 剪映 6.0 起把 draft_content.json 改成加密载荷（base64 串，不以 { 开头）。
# 11.x 在真实草稿上确认是这种形态。这个门槛用来决定「该写哪个文件」。
MODERN_MAJOR = 6

# 注册表里剪映的显示名
APP_NAMES = ("剪映专业版", "剪映", "JianyingPro")


DRAFT_TOKEN = "%JIANYING_DRAFTS%"   # 占位符：指向剪映当前配置的草稿库
_resolving = False


def expand_path(p: str | Path) -> Path:
    """展开 %VAR% 和 ~。

    project.json / plan.json 里允许写 `%LOCALAPPDATA%\\JianyingPro\\...`，
    换台机器只要剪映装在默认位置就不用改配置。

    另外认一个 `%JIANYING_DRAFTS%`：指向剪映当前实际在用的草稿库
    （用户在剪映里改过草稿位置时，默认路径是不对的）。
    """
    s = str(p)
    global _resolving
    if DRAFT_TOKEN in s and not _resolving:
        _resolving = True
        try:
            root = configured_draft_root()
        finally:
            _resolving = False
        if root:
            s = s.replace(DRAFT_TOKEN, str(root))
    return Path(os.path.expandvars(os.path.expanduser(s)))


def portable_path(p: str | Path) -> str:
    """反过来：把本机专属路径写成可移植形式，别把用户名/盘符写进文件。"""
    global _resolving
    s = str(p)
    if not _resolving:
        _resolving = True
        try:
            root = configured_draft_root()
        finally:
            _resolving = False
        if root and s.lower().startswith(str(root).lower()):
            return DRAFT_TOKEN + s[len(str(root)):]

    local = os.environ.get("LOCALAPPDATA")
    if local and s.lower().startswith(local.lower()):
        return "%LOCALAPPDATA%" + s[len(local):]
    return s


def _expand(p: str) -> Path:
    return expand_path(p)


# ---------------------------------------------------------------- 剪映版本

def _registry_versions() -> list[tuple[str, str]]:
    """从卸载注册表里找剪映的 (显示名, 版本号)。非 Windows 返回空。"""
    try:
        import winreg
    except ImportError:
        return []

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    found: list[tuple[str, str]] = []
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, name) as sub:
                            try:
                                disp = winreg.QueryValueEx(sub, "DisplayName")[0]
                            except OSError:
                                continue
                            if not any(a in str(disp) for a in APP_NAMES):
                                continue
                            try:
                                ver = str(winreg.QueryValueEx(sub, "DisplayVersion")[0])
                            except OSError:
                                ver = ""
                            found.append((str(disp), ver))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _version_tuple(v: str) -> tuple[int, ...]:
    parts = []
    for seg in str(v).split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def detect_app() -> dict:
    """找剪映装的是哪个版本。

    版本号决定时间轴文件的加密形态，比「看存量草稿长什么样」可靠——
    存量草稿可能是我们自己写的，拿它当样本等于自证。

    返回 {"version", "major", "era", "source"}；找不到时 version 为空、
    era 为 "unknown"。
    """
    candidates = _registry_versions()
    best = ("", "")
    for disp, ver in candidates:
        if not ver:
            continue
        if _version_tuple(ver) > _version_tuple(best[1] or "0"):
            best = (disp, ver)

    if not best[1]:
        # 注册表没有就退而求其次，看安装目录命名
        for hint in ("%LOCALAPPDATA%\\JianyingPro\\Apps",):
            base = _expand(hint)
            if not base.is_dir():
                continue
            vers = [p.name for p in base.iterdir() if p.is_dir()]
            for name in sorted(vers, key=_version_tuple, reverse=True):
                if _version_tuple(name) > (0,):
                    best = ("安装目录", name)
                    break
            if best[1]:
                break

    major = _version_tuple(best[1])[0] if best[1] else 0
    if not best[1]:
        era = "unknown"
    elif major >= MODERN_MAJOR:
        era = "modern"
    else:
        era = "legacy"

    return {
        "version": best[1],
        "name": best[0],
        "major": major,
        "era": era,
        "source": "registry" if candidates else ("dir" if best[1] else "none"),
    }


# ---------------------------------------------------------------- 草稿库定位

# 剪映把「当前草稿目录」写在用户的全局设置里。这个比任何猜测都权威——
# 实测这台机器上它指向 E:\剪映\JianyingPro Drafts，而默认路径在 C 盘，
# 写错地方剪映根本不显示这个草稿。
GLOBAL_SETTING_HINTS = [
    r"%LOCALAPPDATA%\JianyingPro\User Data\Config\globalSetting",
    r"%APPDATA%\JianyingPro\User Data\Config\globalSetting",
]


def configured_draft_root() -> Path | None:
    """读剪映自己的配置，拿它正在用的草稿目录。"""
    for hint in GLOBAL_SETTING_HINTS:
        p = _expand(hint)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("[", "#", ";")):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in ("currentCustomDraftPath", "customDraftPath"):
                value = value.strip().strip('"')
                if value:
                    cand = Path(value)
                    if cand.is_dir():
                        return cand
    return None


def draft_homes(limit: int = 4000) -> list[dict]:
    """排出「哪个目录才是真库」，配置优先，其余按草稿数量排。

    剪映的 root_meta_info.json 里 all_draft_store 记着每个草稿的真实路径，
    而草稿不一定和索引放在同一个盘（实测：索引在 C 盘、788 个草稿在 E 盘）。
    光看默认路径会把草稿写进一个剪映根本不扫的空库。

    返回 [{"root": Path, "drafts": n, "indexed": bool, "configured": bool}]。
    """
    homes: dict[str, dict] = {}

    def touch(path: Path, *, indexed: bool = False, configured: bool = False) -> dict:
        key = str(path).lower()
        item = homes.setdefault(key, {
            "root": path, "drafts": 0, "indexed": indexed, "configured": configured,
        })
        item["indexed"] = item["indexed"] or indexed
        item["configured"] = item["configured"] or configured
        return item

    # 1) 剪映配置里写死的目录，最高优先级
    conf = configured_draft_root()
    if conf:
        touch(conf, configured=True)

    # 2) 索引里统计草稿实际落盘目录
    for hint in DEFAULT_HINTS:
        idx = _expand(hint) / "root_meta_info.json"
        if not idx.is_file():
            continue
        try:
            meta = json.loads(idx.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in (meta.get("all_draft_store") or [])[:limit]:
            fp = (entry or {}).get("draft_fold_path") or ""
            if not fp:
                continue
            touch(Path(fp).parent, indexed=True)["drafts"] += 1

    # 3) 默认路径本身也进候选（空库也得能选到）
    for hint in DEFAULT_HINTS:
        p = _expand(hint)
        if p.is_dir():
            touch(p)

    out = [h for h in homes.values() if h["root"].is_dir()]
    out.sort(key=lambda h: (h["configured"], h["drafts"], h["indexed"]), reverse=True)
    return out


def guess_draft_root() -> Path | None:
    """挑草稿库：配置写明的优先，其次草稿最多的，最后默认路径。"""
    homes = draft_homes()
    if homes:
        return homes[0]["root"]
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


def _is_ours(draft_dir: Path) -> bool:
    """这个草稿是不是本工具生成的。

    自己写的草稿是明文，不能拿它当「剪映读明文」的证据，
    所以探布局时要能把它挑出来单独算。
    """
    meta = draft_dir / "draft_meta_info.json"
    if not meta.is_file():
        return False
    try:
        m = json.loads(meta.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return False
    return m.get("draft_tool") == MARK


def decide_layout(app: dict, counts: dict) -> tuple[str, str]:
    """按剪映版本决定写哪个时间轴文件。返回 (layout, 依据)。

    layout 取值：
        plaintext  只写 draft_content.json
        info       双写 draft_content.json + draft_info.json
    """
    era = (app or {}).get("era", "unknown")
    ver = (app or {}).get("version") or "未识别"

    if era == "modern":
        return "info", (
            f"剪映 {ver} ≥ {MODERN_MAJOR}.0，时间轴默认是加密载荷；"
            "改成双写 draft_content.json + draft_info.json（都是明文），"
            "新版优先读 draft_info.json"
        )
    if era == "legacy":
        return "plaintext", f"剪映 {ver} < {MODERN_MAJOR}.0，时间轴是明文，只写 draft_content.json"

    native_enc = counts.get("encrypted", 0)
    native_plain = counts.get("plaintext", 0) - counts.get("ours", 0)
    if native_enc > 0 and native_plain <= 0:
        return "info", "没读到剪映版本，但存量草稿是加密的，按双写处理"
    return "plaintext", "没读到剪映版本，存量草稿是明文，按明文写；升级剪映后重跑 doctor 复核"


def inspect(draft_root: Path, limit: int = 200) -> dict:
    """统计草稿库布局。

    返回:
        {
          "root": str,
          "total": int,             # 子目录数
          "sampled": int,           # 实际检查过的草稿数
          "app": {...},             # 剪映版本
          "layout": "plaintext"|"info"|"encrypted"|"empty"|"mixed",
          "layout_reason": str,     # 为什么这么定
          "counts": {"plaintext": n, "encrypted": n, "info-only": n, "ours": n, ...},
          "examples": [{"draft": name, "content": "plaintext", "info": "missing"}],
          "advice": str,
        }
    """
    root = Path(draft_root)
    app = detect_app()
    if not root.is_dir():
        return {
            "root": str(root),
            "error": "草稿库目录不存在",
            "app": app,
            "layout": "missing",
            "layout_reason": "草稿库目录不存在",
            "advice": "在剪映里随便新建一个草稿，再把 project.json 的 draft_folder 指到它所在的目录。",
        }

    subs = [p for p in sorted(root.iterdir())
            if p.is_dir() and not p.name.startswith(".")][:limit]
    counts = {"plaintext": 0, "encrypted": 0, "corrupt": 0,
              "missing": 0, "info-only": 0, "ours": 0}
    examples: list[dict] = []

    for sub in subs:
        c = classify_file(sub / CONTENT)
        i = classify_file(sub / INFO)
        ours = _is_ours(sub)
        shape = {"draft": sub.name, "content": c, "info": i, "ours": ours}
        examples.append(shape)

        if i == "plaintext" and c == "missing":
            counts["info-only"] += 1
        elif c in counts:
            counts[c] += 1
        if ours:
            counts["ours"] += 1

    layout, reason = decide_layout(app, counts)

    if not subs:
        layout, reason = "empty", "草稿库是空的，先写一个草稿验证"
    elif counts["corrupt"] > 0 and counts["plaintext"] == 0 and counts["encrypted"] == 0:
        layout, reason = "mixed", "存量草稿都解析不了，按双写处理并提醒人工确认"

    return {
        "root": str(root),
        "total": len(subs),
        "sampled": len(subs),
        "app": app,
        "layout": layout,
        "layout_reason": reason,
        "counts": counts,
        "examples": examples[:5],
        "advice": _advice(layout, counts, app),
    }


def _advice(layout: str, counts: dict, app: dict | None = None) -> str:
    ver = (app or {}).get("version") or ""
    if layout == "empty":
        return "草稿库是空的。先写一个草稿，打开剪映确认能读到，再按提示定布局。"
    if layout == "plaintext":
        return "只写明文 draft_content.json。剪映升级到 6.0+ 后，重跑 doctor 会改判。"
    if layout == "info":
        return (
            f"双写 draft_content.json + draft_info.json（明文）。"
            f"剪映 {ver or '6.0+'} 的时间轴是加密载荷，本项目不做解密，"
            "改写明文让剪映打开时自行升级；若报「草稿内容已损坏」，"
            "说明这个版本不再接受明文，改用剪映自己新建草稿后再手贴素材。"
        )
    if layout == "mixed":
        return "布局混杂（新旧草稿并存）。双写两个文件名，两边都不落空。"
    if layout == "encrypted":
        return "草稿库已加密，双写明文；打不开就是该版本不再接受明文。"
    return "布局未定，按双写处理。"


def target_filenames(layout: str) -> list[str]:
    """按布局返回该写哪些文件名。"""
    if layout == "plaintext":
        return [CONTENT]
    # info / encrypted / mixed / empty 一律双写：
    # 老版本读 content，新版本读 info，两边都不落空。
    return [CONTENT, INFO]


# 手动覆盖时可接受的写法，统一收敛到两种落盘方式
LAYOUT_CHOICES = {
    "auto": None,          # 自动判定（默认）
    "plaintext": "plaintext",
    "content": "plaintext",
    "info": "info",
    "both": "info",
}


def resolve_layout(explicit: str | None, draft_root: Path) -> tuple[str, str]:
    """决定写哪个时间轴文件。返回 (layout, 依据)。

    显式参数优先：剪映升级后如果自动判定不对，用 `build --layout info`
    或 `--layout plaintext` 直接覆盖，不用改代码。
    """
    choice = (explicit or "auto").strip().lower()
    if choice not in LAYOUT_CHOICES:
        raise ValueError(
            f"不认识的 layout: {explicit}；可选 {'/'.join(LAYOUT_CHOICES)}"
        )
    if choice == "auto":
        info = inspect(draft_root)
        return info.get("layout", "plaintext"), info.get("layout_reason", "")

    layout = LAYOUT_CHOICES[choice]
    files = " + ".join(target_filenames(layout))
    return layout, f"手动指定 {choice}，写 {files}"


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


def fix_draft_content(draft_path: Path) -> dict:
    """把时间轴里的 id 和时长对齐到草稿本身。

    两个都是 pyJianYingDraft 模板带出来的毛病：
    - `content["id"]` 是模板里的固定值，所有草稿共用同一个 id。
      剪映按 id 做缓存/去重，多个草稿同 id 会互相串。
    - `draft_meta_info.json` 的 `tm_duration` 是 0，草稿列表里显示 0:00。

    返回 {"ok","id_fixed","duration"}。
    """
    draft_path = Path(draft_path)
    content_file = draft_path / CONTENT
    meta_file = draft_path / "draft_meta_info.json"
    if not content_file.exists() or not meta_file.exists():
        return {"ok": False, "reason": "缺 draft_content.json 或 draft_meta_info.json"}

    try:
        content = json.loads(content_file.read_text(encoding="utf-8"))
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"解析失败: {exc}"}

    draft_id = str(meta.get("draft_id") or "").strip()
    id_fixed = False
    if draft_id and str(content.get("id") or "") != draft_id:
        content["id"] = draft_id
        id_fixed = True
        content_file.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

    duration = content.get("duration") or 0
    if meta.get("tm_duration") != duration:
        meta["tm_duration"] = duration
        meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    return {"ok": True, "id_fixed": id_fixed, "duration": duration}


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

    # 打个标记：以后探测布局时能认出「这是我们自己写的明文草稿」，
    # 不会拿它当剪映读明文的证据。
    meta["draft_tool"] = MARK
    changed += 1

    meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "fields_fixed": changed}


def _root_source(homes: list[dict], root: Path | None) -> str:
    """说明这个草稿库是怎么选出来的，别让人猜。"""
    if root is None:
        return "没找到"
    key = str(root).lower()
    for h in homes:
        if str(h["root"]).lower() != key:
            continue
        if h.get("configured"):
            return "剪映配置里写明的目录（globalSetting → currentCustomDraftPath）"
        if h.get("drafts"):
            return f"索引里草稿最多的目录（{h['drafts']} 个）"
        return "默认路径"
    return "手动指定"


def doctor(draft_root: Path | None = None) -> dict:
    """环境自检：剪映什么版本、草稿库在哪、能不能写、该写哪个文件。"""
    app = detect_app()
    homes = draft_homes()
    root = Path(draft_root) if draft_root else guess_draft_root()
    report: dict = {
        "app": app,
        "draft_root": str(root) if root else None,
        "homes": [
            {"root": str(h["root"]), "drafts": h["drafts"],
             "configured": h["configured"], "indexed": h["indexed"]}
            for h in homes[:3]
        ],
        "draft_root_source": _root_source(homes, root),
    }

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
