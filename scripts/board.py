# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""只读工作台：把所有项目扫成一个本地应用界面。

为什么是「只读」：

看板和操作分开。能点按钮跑 ingest/cut/build 的控制台听起来省事，但误点一次
就会覆盖一版草稿，而且长任务（转写、下模型）在浏览器里失败时很难看出是哪一步。
所以这里只做三件事：**看走到哪、看该看什么、把命令复制走**。

为什么是「一个自包含 HTML」：

不依赖本地服务、不联网、不发请求。数据在生成时内联进文件，双击就能看，
截图分享也不会漏出本机绝对路径（所有路径一律相对项目目录）。

    python scripts/board.py            # 生成 老虎AI爆款·内容专家.html
    python scripts/board.py --root .   # 指定扫描根目录
"""

from __future__ import annotations

import base64
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import metrics as metrics_mod  # noqa: E402

SKIP_DIRS = {".venv", ".workbuddy", ".backup", ".git", "__pycache__", "node_modules",
             ".idea", ".obsidian"}

STEP_ORDER = ["选题", "素材", "转写", "切点", "草稿", "发布稿", "数据"]

PLATFORM_FILES = {
    "douyin.md": "抖音",
    "xhs.md": "小红书",
    "shipinhao.md": "视频号",
    "x.md": "X",
}

BRAND = {
    "name": "TigerVideo",
    "sub_before": "老虎 ",
    "sub_accent": "AI",
    "sub_after": " 爆款 · 内容专家",
}

OUT_NAME = "老虎AI爆款·内容专家.html"


# ---------------------------------------------------------------- 扫描

def find_projects(root: Path, max_depth: int = 3) -> list[Path]:
    """找出所有含 project.json 的目录。跳过虚拟环境和缓存目录。"""
    found: list[Path] = []
    for path in root.rglob("project.json"):
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if len(rel_parts) > max_depth + 1:
            continue
        found.append(path.parent)
    return sorted(set(found))


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _rel(path: Path, root: Path) -> str:
    """统一成正斜杠的相对路径，网页里既能当链接又能当命令参数。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _broll_left(project: Path) -> int | None:
    md = project / "05-plan" / "B-roll待补清单.md"
    if not md.exists():
        return None
    m = re.search(r"还差\s*(\d+)\s*处", md.read_text(encoding="utf-8", errors="ignore"))
    return int(m.group(1)) if m else None


def project_summary(project: Path, root: Path) -> dict:
    rel = _rel(project, root)
    win = rel.replace("/", "\\")
    cfg = _load_json(project / "project.json")

    topic_card = project / "00-intel" / "选题卡.md"
    raw = project / "02-raw" / "main.mp4"
    transcript = _load_json(project / "03-transcript" / "transcript.json")
    cut = _load_json(project / "04-cut" / "cutlist.json")
    plan = _load_json(project / "05-plan" / "plan.json")
    post = project / "06-publish" / "post.json"
    mpath = project / "07-metrics" / "metrics.csv"

    metric_rows = metrics_mod.read_rows([mpath]) if mpath.exists() else []
    rates = [r["completion_rate"] for r in metric_rows if r.get("completion_rate") is not None]

    done = {
        "选题": topic_card.exists(),
        "素材": raw.exists(),
        "转写": bool(transcript.get("segments")),
        "切点": bool(cut.get("units")),
        "草稿": bool(plan.get("shots")),
        "发布稿": post.exists(),
        "数据": bool(metric_rows),
    }
    next_step = next((s for s in STEP_ORDER if not done[s]), None)

    cmds = {
        "选题": f".\\run.cmd intel rank {win}  然后 topic pick",
        "素材": f"把主机位放到 {win}\\02-raw\\main.mp4",
        "转写": f".\\run.cmd ingest {win}",
        "切点": f".\\run.cmd cut {win}",
        "草稿": f".\\run.cmd build {win}",
        "发布稿": f".\\run.cmd pack {win}",
        "数据": f".\\run.cmd metrics log {win} --platform douyin --views 0 --completion 0%",
    }

    units = cut.get("units") or []
    kept = sum(1 for u in units if u.get("keep"))
    warnings = cut.get("warnings") or []
    shots = plan.get("shots") or []
    angles = sorted({Path(s.get("video") or s.get("image") or "").name
                     for s in shots if s.get("video") or s.get("image")})

    arts = [
        ("切点对照表", project / "04-cut" / "切点对照表.md"),
        ("B-roll 清单", project / "05-plan" / "B-roll待补清单.md"),
        ("发布前检查", project / "06-publish" / "发布前检查.md"),
    ]
    artifacts = [{"label": label, "href": _rel(path, root)} for label, path in arts if path.exists()]
    publish = [
        {"label": zh, "href": _rel(project / "06-publish" / fn, root)}
        for fn, zh in PLATFORM_FILES.items() if (project / "06-publish" / fn).exists()
    ]

    return {
        "name": cfg.get("name") or project.name,
        "rel": rel,
        "win": win,
        "draft_name": plan.get("name") or cfg.get("name") or project.name,
        "done": done,
        "done_count": sum(1 for v in done.values() if v),
        "next_step": next_step,
        "next_cmd": cmds.get(next_step, ""),
        "canvas": f"{cfg.get('canvas', {}).get('width', 1080)}×{cfg.get('canvas', {}).get('height', 1920)}",
        "segments": len(transcript.get("segments") or []),
        "units": len(units),
        "kept": kept,
        "cut_in": cut.get("total_in"),
        "cut_out": cut.get("total_out"),
        "cut_warnings": warnings,
        "shots": len(shots),
        "angles": angles,
        "broll_left": _broll_left(project),
        "artifacts": artifacts,
        "publish": publish,
        "metric_rows": len(metric_rows),
        "metric_median": metrics_mod._median(rates),
        "metrics_csv": _rel(mpath, root) if mpath.exists() else "",
    }


def scan_vault(root: Path) -> dict:
    """扫知识库。只列笔记，不读正文（工作台不需要复述笔记内容）。"""
    base = root / "知识库"
    if not base.is_dir():
        return {"exists": False, "groups": [], "total": 0}
    groups: dict[str, list[dict]] = {}
    for md in sorted(base.rglob("*.md")):
        rel_parts = md.relative_to(base).parts
        if any(p in SKIP_DIRS for p in rel_parts):
            continue
        if len(rel_parts) == 1:
            group = "根目录"
        else:
            group = rel_parts[0]
        text = md.read_text(encoding="utf-8", errors="ignore")[:400]
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = m.group(1).strip() if m else md.stem
        groups.setdefault(group, []).append({
            "title": title,
            "href": _rel(md, root),
            "stem": md.stem,
        })
    ordered = [{"name": k, "notes": v} for k, v in sorted(groups.items()) if k != "根目录"]
    roots = groups.get("根目录")
    if roots:
        ordered.insert(0, {"name": "", "notes": roots})
    return {"exists": True, "groups": ordered, "total": sum(len(v) for v in groups.values())}


def scan_topics(root: Path) -> dict:
    """读「已定选题.md」的表格。只读，不写回。"""
    path = root / "知识库" / "01-选题" / "已定选题.md"
    rows: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 7:
                continue
            if cells[0] in ("日期", "") or set(cells[0]) <= {"-", ":"}:
                continue
            if not any(cells):
                continue
            rows.append({
                "date": cells[0], "title": cells[1], "type": cells[2],
                "platform": cells[3], "verdict": cells[4],
                "project": cells[5], "status": cells[6],
            })
    return {"href": _rel(path, root) if path.exists() else "", "rows": rows,
            "exists": path.exists()}


def env_info() -> dict:
    """只读出环境事实，不跑任何写操作。"""
    info = {"python": sys.version.split()[0], "ffmpeg": "—", "engine": "—",
            "draft_layout": "—", "draft_writable": "—", "draft_name": "—"}
    try:
        import pyJianYingDraft  # noqa: F401
        info["engine"] = "OK"
    except ImportError:
        info["engine"] = "缺失"
    try:
        import imageio_ffmpeg
        info["ffmpeg"] = "OK"
    except ImportError:
        info["ffmpeg"] = "缺失"
    try:
        import draft_compat
        d = draft_compat.doctor()
        info["draft_layout"] = d.get("layout") or "—"
        info["draft_writable"] = "可写" if d.get("writable") else "不可写"
        info["draft_name"] = Path(str(d.get("draft_root") or "")).name or "—"
    except Exception as exc:                     # noqa: BLE001
        info["draft_layout"] = f"探测失败: {exc}"
    return info


def collect(root: Path) -> dict:
    projects = [project_summary(p, root) for p in find_projects(root)]

    csvs = [Path(root) / p["metrics_csv"] for p in projects if p["metrics_csv"]]
    rows = metrics_mod.read_rows(csvs)
    analysis = metrics_mod.analyse(rows) if rows else {
        "total": 0, "platform_stats": {}, "type_stats": {}, "cell_stats": {},
        "downgrade": [], "thin_types": [], "first_platform": [],
    }

    recent = sorted(rows, key=lambda r: r.get("date") or "", reverse=True)[:10]

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "root_name": root.name,
        "projects": projects,
        "analysis": analysis,
        "rows": rows,
        "recent": recent,
        "vault": scan_vault(root),
        "topics": scan_topics(root),
        "env": env_info(),
    }


# ---------------------------------------------------------------- 图表

def _bar_chart(items: list[tuple[str, float | None]], width: int = 620) -> str:
    if not items:
        return ""
    bar_h, gap, label_w = 22, 12, 78
    height = len(items) * (bar_h + gap) + 8
    max_w = width - label_w - 60
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i, (label, value) in enumerate(items):
        y = i * (bar_h + gap) + 4
        parts.append(f'<text x="0" y="{y + 15}" font-size="13" fill="#4b5563">{html.escape(label)}</text>')
        if value is None:
            parts.append(f'<text x="{label_w}" y="{y + 15}" font-size="13" fill="#9ca3af">无数据</text>')
            continue
        w = max(2, round(max_w * value))
        parts.append(f'<rect x="{label_w}" y="{y + 2}" width="{max_w}" height="{bar_h - 4}" rx="4" fill="#f1f3f6"/>')
        parts.append(f'<rect x="{label_w}" y="{y + 2}" width="{w}" height="{bar_h - 4}" rx="4" fill="#d92b1f"/>')
        parts.append(f'<text x="{label_w + w + 8}" y="{y + 15}" font-size="12" fill="#374151">{value * 100:.0f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def _line_chart(points: list[tuple[str, float]], width: int = 620, height: int = 170) -> str:
    if len(points) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 34, 12, 12, 26
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    vals = [v for _, v in points]
    lo = max(0.0, min(vals) - 0.05)
    hi = min(1.0, max(vals) + 0.05)
    span = max(hi - lo, 0.01)
    step = plot_w / (len(points) - 1)

    coords = []
    for i, (_, v) in enumerate(points):
        x = pad_l + i * step
        y = pad_t + plot_h * (1 - (v - lo) / span)
        coords.append((x, y, v))

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for frac in (0, 0.5, 1):
        y = pad_t + plot_h * frac
        val = hi - span * frac
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="#eceef1" stroke-width="1"/>')
        parts.append(f'<text x="0" y="{y + 4:.1f}" font-size="11" fill="#9ca3af">{val * 100:.0f}%</text>')
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in coords)
    parts.append(f'<polyline points="{poly}" fill="none" stroke="#d92b1f" stroke-width="2" stroke-linejoin="round"/>')
    for x, y, _ in coords:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#d92b1f"/>')
    for idx in {0, len(points) - 1}:
        label = points[idx][0][5:] or points[idx][0]
        anchor = "start" if idx == 0 else "end"
        parts.append(f'<text x="{coords[idx][0]:.1f}" y="{height - 6}" font-size="11" fill="#9ca3af" '
                     f'text-anchor="{anchor}">{html.escape(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- 片段

def _esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def _fmt_sec(value) -> str:
    return "—" if value is None else f"{value:.1f}s"


def _logo_data_uri(root: Path) -> str:
    p = root / "assets" / "logo-128.png"
    if not p.exists():
        return ""
    try:
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return ""


def _steps(done: dict, compact: bool = False) -> str:
    cls = "steps compact" if compact else "steps"
    return f'<div class="{cls}">' + "".join(
        f'<span class="step{" done" if done[s] else ""}"><span class="dot"></span>{s}</span>'
        for s in STEP_ORDER
    ) + "</div>"


def _copy_btn(cmd: str, label: str = "复制") -> str:
    return (f'<button class="ghost" data-cmd="{_esc(cmd)}" onclick="copyText(this)">'
            f'{label}</button>')


def render_project_card(p: dict) -> str:
    stats = []
    if p["segments"]:
        stats.append(f'<span><i>句数</i>{p["segments"]}</span>')
    if p["units"]:
        stats.append(f'<span><i>保留</i>{p["kept"]}/{p["units"]}</span>')
    if p["cut_in"] and p["cut_out"]:
        saved = (1 - p["cut_out"] / p["cut_in"]) * 100
        stats.append(f'<span><i>时长</i>{_fmt_sec(p["cut_in"])} → {_fmt_sec(p["cut_out"])}'
                     f'<em>省 {saved:.0f}%</em></span>')
    if p["shots"]:
        stats.append(f'<span><i>镜数</i>{p["shots"]}</span>')
    if p["angles"]:
        stats.append(f'<span><i>机位</i>{" / ".join(_esc(a) for a in p["angles"])}</span>')
    if p["broll_left"] is not None:
        stats.append(f'<span><i>B-roll</i>{"已盖全" if p["broll_left"] == 0 else f"还差 {p["broll_left"]} 处"}</span>')
    if p["metric_rows"]:
        median = f'{p["metric_median"] * 100:.0f}%' if p["metric_median"] is not None else "—"
        stats.append(f'<span><i>数据</i>{p["metric_rows"]} 条 · 中位 {median}</span>')

    warns = "".join(f'<p class="warn">⚠ {_esc(w)}</p>' for w in p["cut_warnings"][:3])

    if p["next_step"]:
        next_html = (f'<div class="next"><span class="muted">下一步 · {p["next_step"]}</span>'
                     f'<code>{_esc(p["next_cmd"])}</code>{_copy_btn(p["next_cmd"])}</div>')
    else:
        next_html = '<div class="next done-next"><span class="muted">六步产物齐了，打开剪映精剪，然后发。</span></div>'

    links = "".join(f'<a href="{_esc(a["href"])}" target="_blank">{_esc(a["label"])}</a>'
                    for a in p["artifacts"])
    links += "".join(f'<a href="{_esc(a["href"])}" target="_blank">{_esc(a["label"])}稿</a>'
                     for a in p["publish"])
    links_html = f'<div class="links">{links}</div>' if links else ""

    draft = f'<span class="draft">剪映草稿 · {_esc(p["draft_name"])}</span>' if p["draft_name"] else ""

    return f"""
    <article class="card project">
      <div class="card-head">
        <h3>{_esc(p["name"])}</h3>
        <span class="path">{_esc(p["rel"])}</span>
      </div>
      {_steps(p["done"])}
      <div class="stats">{"".join(stats) or '<span class="muted">还没有素材</span>'}</div>
      {warns}
      {next_html}
      {links_html}
      {f'<div class="foot">{draft}</div>' if draft else ''}
    </article>"""


def render_today(data: dict) -> str:
    projects = data["projects"]
    rows = data["rows"]
    done_all = sum(1 for p in projects if all(p["done"].values()))
    running = sum(1 for p in projects if 0 < p["done_count"] < len(STEP_ORDER))

    if projects:
        focus = min(projects, key=lambda p: p["done_count"])
    else:
        focus = None

    if focus:
        focus_html = f"""
        <div class="card focus">
          <div class="card-head">
            <h3>{_esc(focus["name"])}</h3>
            <span class="path">{_esc(focus["rel"])}</span>
          </div>
          {_steps(focus["done"])}
          <div class="next"><span class="muted">下一步 · {_esc(focus["next_step"] or "已完成")}</span>
            <code>{_esc(focus["next_cmd"])}</code>{_copy_btn(focus["next_cmd"])}</div>
        </div>"""
    else:
        focus_html = """
        <div class="card empty-card">
          <p><b>还没有项目。</b>建一个就开始：</p>
          <div class="next"><code>.\\run.cmd init 我的第01期 --name 第01期</code>
            <button class="ghost" data-cmd=".\\run.cmd init 我的第01期 --name 第01期" onclick="copyText(this)">复制</button></div>
        </div>"""

    list_rows = "".join(
        f'<div class="row"><a href="#projects">{_esc(p["name"])}</a>'
        f'<span class="row-steps">{"".join(f"<i class=\"{'on' if p['done'][s] else ''}\"></i>" for s in STEP_ORDER)}</span>'
        f'<span class="row-next">{_esc(p["next_step"] or "已完成")}</span></div>'
        for p in projects
    ) or '<p class="muted">暂无</p>'

    recent_html = ""
    if data["recent"]:
        items = "".join(
            f'<div class="row"><span class="row-date">{_esc(r.get("date") or "—")}</span>'
            f'<span>{_esc(metrics_mod.LABELS.get(r.get("platform"), r.get("platform")))}</span>'
            f'<span class="row-title">{_esc(r.get("title") or "—")}</span>'
            f'<span class="num">{(r["completion_rate"] * 100):.0f}%</span></div>'
            for r in data["recent"][:5]
        )
        recent_html = f'<div class="card"><h3 class="small">最近数据</h3><div class="rows">{items}</div></div>'

    return f"""
    <div class="page-head">
      <h1>今日</h1>
      <p class="sub">灰点是还没做的，红点是做完了的。</p>
    </div>
    <div class="kpis">
      <div class="kpi"><b>{len(projects)}</b><span>项目</span></div>
      <div class="kpi"><b>{running}</b><span>进行中</span></div>
      <div class="kpi"><b>{done_all}</b><span>六步走完</span></div>
      <div class="kpi"><b>{len(rows)}</b><span>数据记录</span></div>
    </div>
    {focus_html}
    <h2>项目进度</h2>
    <div class="card"><div class="rows">{list_rows}</div></div>
    {recent_html}
    """


def render_topics(data: dict) -> str:
    topics = data["topics"]
    rows = topics["rows"]
    if rows:
        body = "".join(
            f'<tr><td>{_esc(r["date"])}</td><td>{_esc(r["title"])}</td>'
            f'<td class="num">{_esc(r["type"])}</td><td>{_esc(r["platform"])}</td>'
            f'<td>{_esc(r["verdict"])}</td><td class="muted">{_esc(r["status"])}</td>'
            f'<td class="mono muted">{_esc(r["project"])}</td></tr>'
            for r in rows
        )
        table = f"""<div class="card table-card"><table>
          <thead><tr><th>日期</th><th>选题</th><th>类型</th><th>主发</th><th>结论</th><th>状态</th><th>项目</th></tr></thead>
          <tbody>{body}</tbody></table></div>"""
    else:
        table = """
        <div class="card empty-card">
          <p><b>还没有定下来的选题。</b>选题不是找灵感，是找「有人正在为这件事花时间」。</p>
          <p class="note">在对话里说一句领域和平台，会拿到 3–5 个选题卡，每个都带「做 / 不做 / 换角度」的结论。</p>
        </div>"""

    return f"""
    <div class="page-head"><h1>选题</h1>
      <p class="sub">情报 → 3–5 个候选 → 结论。不做的也留，下周不重想。</p></div>
    <div class="card tip">
      <p style="margin:0 0 6px"><b>判断依据在笔记里：</b>四栏（一句话选题 / 具体观众 / 反常识点 / 素材清单）缺一栏就是没想清。</p>
      <p class="note" style="margin:0">规矩和写法见 <a href="知识库/01-选题/选题判断.md" target="_blank">选题判断</a>，
      归档表见 <a href="{_esc(topics["href"])}" target="_blank">已定选题</a>。</p>
    </div>
    {table}
    <div class="card">
      <h3 class="small">一轮怎么做</h3>
      <ol class="notes">
        <li><b>取情报</b>：平台热榜、对标账号、你的历史数据（数据页的中位数就是依据）。</li>
        <li><b>出候选</b>：3–5 个，每个写清目标观众和反常识点，不许「年轻人」这种说法。</li>
        <li><b>下结论</b>：做 / 不做 / 换角度。不做的写一句为什么。</li>
        <li><b>定稿归档</b>：填进 <code>知识库/01-选题/已定选题.md</code>，状态从「选题」开始。</li>
      </ol>
    </div>
    """


def render_publish(data: dict) -> str:
    cards = []
    for p in data["projects"]:
        have = {a["label"]: a["href"] for a in p["publish"]}
        chips = []
        for zh in PLATFORM_FILES.values():
            if zh in have:
                chips.append(f'<a class="plat on" href="{_esc(have[zh])}" target="_blank">{zh}</a>')
            else:
                chips.append(f'<span class="plat">{zh}</span>')
        check = next((a["href"] for a in p["artifacts"] if a["label"] == "发布前检查"), "")
        check_html = (f'<a class="ghost-link" href="{_esc(check)}" target="_blank">发布前检查表</a>'
                      if check else '<span class="muted small">还没有检查表</span>')
        dur = f'{p["cut_out"]:.1f}s' if p["cut_out"] else "—"
        cards.append(f"""
        <article class="card">
          <div class="card-head"><h3>{_esc(p["name"])}</h3>
            <span class="path">{_esc(p["rel"])}</span></div>
          <div class="plats">{"".join(chips)}</div>
          <div class="stats" style="margin-top:10px">
            <span><i>成片时长</i>{dur}</span>
            <span><i>已出稿</i>{len(p["publish"])}/4</span>
          </div>
          <div class="links">{check_html}</div>
        </article>""")

    if not cards:
        cards.append("""
        <div class="card empty-card">
          <p><b>还没有发布稿。</b>先出粗剪，再生成四平台文案：</p>
          <div class="next"><code>.\\run.cmd pack 我的第01期</code>
            <button class="ghost" data-cmd=".\\run.cmd pack 我的第01期" onclick="copyText(this)">复制</button></div>
        </div>""")

    return f"""
    <div class="page-head"><h1>发布</h1>
      <p class="sub">系统只出稿和检查表，最后那一下点击留给你。</p></div>
    <div class="card tip">
      <p style="margin:0">同一条内容不是复制四遍：抖音改前三秒冲突，小红书改「值得收藏」，视频号改「转给谁」，X 改观点。
      平台字数与话题数见 <a href="知识库/03-技巧/平台规则.md" target="_blank">平台规则</a>。</p>
    </div>
    {"".join(cards)}
    """


def render_data(data: dict) -> str:
    analysis = data["analysis"]
    rows = data["rows"]
    if not rows:
        return """
        <div class="page-head"><h1>数据</h1><p class="sub">发完 7 天记一次，不补记。</p></div>
        <div class="card empty-card">
          <p><b>还没有数据。</b>发布 7 天后，按平台各记一行：</p>
          <div class="next"><code>.\\run.cmd metrics log 我的第01期 --platform douyin --views 12000 --completion 42%</code>
            <button class="ghost" data-cmd=".\\run.cmd metrics log 我的第01期 --platform douyin --views 12000 --completion 42%" onclick="copyText(this)">复制</button></div>
          <p class="note">满 6 条才会给「降权 / 首发」结论，不够就说不够——不拿两条数据调方向。</p>
        </div>"""

    plat_items = [(metrics_mod.LABELS.get(p, p), st["median"])
                  for p, st in sorted(analysis["platform_stats"].items(),
                                      key=lambda kv: -(kv[1]["median"] or 0))]
    bars = _bar_chart(plat_items)
    points = [(r.get("date") or "", r["completion_rate"])
              for r in sorted(rows, key=lambda r: r.get("date") or "")
              if r.get("completion_rate") is not None and r.get("date")][-24:]
    line = _line_chart(points)

    if analysis["downgrade"]:
        items = "".join(
            f'<p class="warn">⚠ {_esc(d["label"])}：最近 {len(d["recent"])} 条 '
            f'{" / ".join(f"{v * 100:.0f}%" for v in d["recent"])}，低于此前基线 '
            f'{d["median"] * 100:.0f}%</p>'
            for d in analysis["downgrade"]
        )
        thin = "、".join(analysis["thin_types"])
        downgrade = (f'<div class="card"><h3 class="small">建议降权</h3>{items}'
                     '<p class="note">减量而不是停掉。停掉就分不清是选题问题还是执行问题。</p>'
                     + (f'<p class="note">样本不足，暂不判断：{_esc(thin)}</p>' if thin else "")
                     + "</div>")
    else:
        thin = "、".join(analysis["thin_types"]) or "无"
        downgrade = (f'<div class="card"><h3 class="small">建议降权</h3>'
                     f'<p class="muted small">暂无。样本不足的类型：{_esc(thin)}（每类满 6 条才判断）</p></div>')

    recent = "".join(
        f'<tr><td>{_esc(r.get("date") or "—")}</td>'
        f'<td>{_esc(metrics_mod.LABELS.get(r.get("platform"), r.get("platform")))}</td>'
        f'<td>{_esc(r.get("title") or "—")}</td>'
        f'<td class="num">{_esc(r.get("topic_type") or "—")}</td>'
        f'<td class="num">{(r["completion_rate"] * 100):.0f}%</td>'
        f'<td class="num">{int(r["views"]) if r.get("views") is not None else "—"}</td></tr>'
        for r in data["recent"]
    )

    return f"""
    <div class="page-head"><h1>数据</h1><p class="sub">共 {len(rows)} 条。中位数比均值可靠——一条爆款会把均值拉得没法看。</p></div>
    <div class="card">
      <h3 class="small">各平台完播中位数</h3>
      {bars or '<p class="muted small">没有可算的中位数</p>'}
      {('<h3 class="small" style="margin-top:18px">完播趋势</h3>' + line) if line else ""}
    </div>
    {downgrade}
    <h2>最近记录</h2>
    <div class="card table-card">
      <table>
        <thead><tr><th>日期</th><th>平台</th><th>标题</th><th>类型</th><th>完播</th><th>播放</th></tr></thead>
        <tbody>{recent}</tbody>
      </table>
    </div>
    """


def render_knowledge(data: dict) -> str:
    vault = data["vault"]
    if not vault["exists"]:
        return """
        <div class="page-head"><h1>知识库</h1><p class="sub">还没建。</p></div>
        <div class="card empty-card"><p>跑一次 <code>python scripts/board.py</code> 之前，先建 <code>知识库/</code>。</p></div>"""

    groups = ""
    for g in vault["groups"]:
        name = g["name"] or "总览"
        notes = "".join(
            f'<li><a href="{_esc(n["href"])}" target="_blank">{_esc(n["title"])}</a></li>'
            for n in g["notes"]
        )
        groups += (f'<div class="card"><h3 class="small">{_esc(name)}'
                   f'<span class="tag">{len(g["notes"])}</span></h3><ul class="notes">{notes}</ul></div>')

    return f"""
    <div class="page-head"><h1>知识库</h1>
      <p class="sub">{vault["total"]} 篇。这里是判断的依据，不是产物的仓库。</p></div>
    <div class="card tip">
      <p>用 Obsidian 打开 <code>知识库</code> 文件夹（不是网页）。这里只是目录，改内容去 Obsidian。</p>
    </div>
    {groups}
    """


def render_system(data: dict) -> str:
    env = data["env"]
    commands = [
        ("doctor", ".\\run.cmd doctor", "环境与草稿库自检"),
        ("init", ".\\run.cmd init 我的第01期 --name 第01期", "建项目骨架"),
        ("ingest", ".\\run.cmd ingest 我的第01期", "转写 + 音轨标准化"),
        ("cut", ".\\run.cmd cut 我的第01期", "算切点，出对照表"),
        ("build", ".\\run.cmd build 我的第01期", "铺画面 + 出草稿"),
        ("verify", ".\\run.cmd verify 我的第01期", "交付前校验"),
        ("pack", ".\\run.cmd pack 我的第01期", "四平台发布稿"),
        ("metrics", ".\\run.cmd metrics report --all .", "数据汇总"),
        ("board", ".\\run.cmd board", "重新生成本页"),
    ]
    cmd_rows = "".join(
        f'<tr><td class="mono">{_esc(name)}</td><td class="mono cmd">{_esc(cmd)}</td>'
        f'<td class="muted">{_esc(desc)}</td>'
        f'<td>{_copy_btn(cmd, "复制")}</td></tr>'
        for name, cmd, desc in commands
    )

    return f"""
    <div class="page-head"><h1>系统</h1><p class="sub">只读探测，不改任何东西。</p></div>
    <div class="card">
      <h3 class="small">环境</h3>
      <table class="kv">
        <tbody>
          <tr><td class="muted">Python</td><td class="mono">{_esc(env["python"])}</td></tr>
          <tr><td class="muted">草稿引擎</td><td>{_esc(env["engine"])}</td></tr>
          <tr><td class="muted">ffmpeg</td><td>{_esc(env["ffmpeg"])}</td></tr>
          <tr><td class="muted">草稿库</td><td class="mono">{_esc(env["draft_name"])}</td></tr>
          <tr><td class="muted">草稿库布局</td><td><code>{_esc(env["draft_layout"])}</code></td></tr>
          <tr><td class="muted">可写</td><td>{_esc(env["draft_writable"])}</td></tr>
        </tbody>
      </table>
    </div>
    <h2>命令速查</h2>
    <div class="card table-card">
      <table>
        <thead><tr><th>步骤</th><th>命令</th><th>做什么</th><th></th></tr></thead>
        <tbody>{cmd_rows}</tbody>
      </table>
    </div>
    <div class="card">
      <h3 class="small">边界</h3>
      <ul class="notes">
        <li>不导出、不发布。导出在剪映点，发布四个平台手动点——自动发布的失效是静默的。</li>
        <li>草稿只写到剪映草稿库，不逆向加密。剪映 6.0+ 按布局写对文件名。</li>
        <li>本页是快照，跑完一步要重新生成。它不联网、不发请求。</li>
      </ul>
    </div>
    """


# ---------------------------------------------------------------- 页面

CSS = """
:root{
  --red:#d92b1f; --red-dark:#b8231a; --red-tint:#fdf0ee;
  --text:#1f2328; --muted:#6b7280; --faint:#9ca3af;
  --line:#e8eaee; --bg:#f6f7f9; --card:#fff;
  --radius:12px;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);
  font:14px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:inherit}
.app{display:flex;min-height:100vh}

/* 侧栏 */
.side{width:236px;flex:0 0 236px;background:#fbfbfc;border-right:1px solid var(--line);
  padding:18px 12px 14px;display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.brand{display:flex;align-items:center;gap:10px;padding:2px 6px 18px}
.brand img{width:34px;height:34px;border-radius:50%;display:block}
.brand .logo-fallback{width:34px;height:34px;border-radius:50%;background:var(--red);color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px}
.brand b{display:block;font-size:15px;letter-spacing:.2px;line-height:1.2}
.brand small{display:block;color:var(--muted);font-size:11px;margin-top:2px;letter-spacing:.2px}
.brand .ai{color:var(--red);font-weight:700;letter-spacing:.3px}
.nav-group{margin-bottom:14px}
.nav-label{font-size:11px;color:var(--faint);padding:0 10px 6px;letter-spacing:.4px}
.nav a{display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:8px;
  color:#3f4650;text-decoration:none;font-size:13.5px;margin-bottom:2px}
.nav a svg{opacity:.75}
.nav a:hover{background:#f1f2f4}
.nav a.active{background:var(--red-tint);color:var(--red-dark);font-weight:600}
.nav a.active svg{opacity:1}
.side-foot{margin-top:auto;padding:10px 10px 0;border-top:1px solid var(--line);
  color:var(--faint);font-size:11px;line-height:1.5}

/* 主区 */
.main{flex:1;min-width:0;padding:26px 30px 60px}
.inner{max-width:880px;margin:0 auto}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;
  margin-bottom:18px;flex-wrap:wrap}
.topbar .meta{color:var(--faint);font-size:12px}
.topbar .meta code{background:#eef0f3;padding:2px 6px;border-radius:5px;font-size:11px}
.page-head{margin:0 0 16px}
.page{display:none}
.page.active{display:block}
.page-head h1{margin:0;font-size:22px;letter-spacing:.3px}
.page-head .sub{margin:4px 0 0;color:var(--muted);font-size:13px}
h2{font-size:14px;margin:26px 0 10px;color:#111827;letter-spacing:.3px}

.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:13px 15px}
.kpi b{display:block;font-size:23px;line-height:1.2;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:12px}

.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px 18px;margin-bottom:12px}
.card h3{margin:0;font-size:15px}
.card h3.small{font-size:13px;color:#374151;margin-bottom:10px;letter-spacing:.3px}
.card-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}
.path{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:var(--faint)}
.focus{border-color:#f0d9d6;box-shadow:0 1px 2px rgba(217,43,31,.04)}
.empty-card{border-style:dashed;color:var(--muted)}
.tip{background:#fffdf7;border-color:#f3e7cd}

.steps{display:flex;gap:6px;margin:12px 0 10px;flex-wrap:wrap}
.step{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--faint);
  background:#f3f4f6;border-radius:999px;padding:3px 9px}
.step.done{background:#fdf0ee;color:var(--red-dark)}
.dot{width:5px;height:5px;border-radius:50%;background:#d8dbe0}
.step.done .dot{background:var(--red)}

.stats{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:#374151;margin-bottom:8px}
.stats i{font-style:normal;color:var(--faint);margin-right:5px}
.stats em{font-style:normal;color:var(--faint);margin-left:6px}
.warn{color:#b45309;font-size:12px;margin:3px 0}
.muted{color:var(--muted)}
.small{font-size:12.5px}

.next{display:flex;align-items:center;gap:8px;background:#f8f9fb;border:1px solid var(--line);
  border-radius:8px;padding:8px 10px;margin-top:8px}
.next code{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#1f2937;
  flex:1;overflow-wrap:anywhere}
.done-next{background:#f6fbf7;border-color:#dcefe2}

button{cursor:pointer;font:inherit}
.ghost{border:1px solid #dcdfe4;background:#fff;border-radius:6px;padding:3px 9px;
  font-size:11.5px;color:#4b5563;white-space:nowrap}
.ghost:hover{border-color:#b9bec6;color:#111827}
.ghost.ok{border-color:var(--red);color:var(--red)}

.links{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}
.links a{font-size:11.5px;color:var(--red-dark);text-decoration:none;border:1px solid #f2dcd9;
  background:#fffafa;border-radius:6px;padding:3px 9px}
.links a:hover{background:var(--red-tint)}
.plats{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.plat{font-size:12px;border:1px solid var(--line);border-radius:6px;padding:3px 10px;color:#b6bcc5}
.plat.on{color:var(--red-dark);border-color:#f2dcd9;background:#fffafa;text-decoration:none}
.plat.on:hover{background:var(--red-tint)}
.ghost-link{font-size:11.5px;color:var(--red-dark);text-decoration:none;border:1px solid #f2dcd9;
  border-radius:6px;padding:3px 9px;background:#fffafa}
.ghost-link:hover{background:var(--red-tint)}
.foot{margin-top:9px;padding-top:9px;border-top:1px dashed #eceef1}
.draft{font-size:11.5px;color:var(--faint)}

.rows{display:flex;flex-direction:column}
.row{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #f2f3f5;font-size:13px}
.row:last-child{border-bottom:0}
.row a{text-decoration:none;font-weight:500;min-width:120px}
.row a:hover{color:var(--red-dark)}
.row-steps{display:flex;gap:4px}
.row-steps i{width:7px;height:7px;border-radius:50%;background:#e2e5e9;display:block}
.row-steps i.on{background:var(--red)}
.row-next{color:var(--faint);font-size:12px;margin-left:auto}
.row-date{color:var(--faint);font-size:12px;width:88px}
.row-title{color:#374151;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.num{font-variant-numeric:tabular-nums}

.table-card{padding:6px 10px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:8px;border-bottom:1px solid #f2f3f5}
th{color:var(--faint);font-weight:500;font-size:11.5px}
tr:last-child td{border-bottom:0}
td.num{font-variant-numeric:tabular-nums}
td.mono,.mono{font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
td.cmd{color:#374151}
table.kv td:first-child{width:132px}
.tag{display:inline-block;margin-left:8px;background:#f1f2f4;color:var(--muted);
  border-radius:999px;padding:0 7px;font-size:11px;font-weight:400}

.notes{margin:0;padding-left:18px;color:#374151;font-size:13px}
.notes li{margin:5px 0}
.notes a{color:var(--red-dark);text-decoration:none}
.notes a:hover{text-decoration:underline}

@media (max-width:860px){
  .app{flex-direction:column}
  .side{width:auto;flex:none;height:auto;position:static;border-right:0;border-bottom:1px solid var(--line)}
  .nav{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px}
  .nav-group{margin:0}
  .nav-label,.side-foot{display:none}
  .main{padding:18px 16px 40px}
  .kpis{grid-template-columns:repeat(2,1fr)}
}
"""

JS = """
function copyText(btn){
  const text = btn.dataset.cmd || '';
  const done = () => { const old = btn.textContent; btn.textContent = '已复制';
    btn.classList.add('ok');
    setTimeout(() => { btn.textContent = old; btn.classList.remove('ok'); }, 1200); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallback(text, done));
  } else { fallback(text, done); }
}
function fallback(text, done){
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch(e) { alert('复制失败，请手动选中'); }
  document.body.removeChild(ta);
}
function showPage(id){
  const pages = document.querySelectorAll('.page');
  let hit = false;
  pages.forEach(p => {
    const on = p.id === 'page-' + id;
    p.classList.toggle('active', on);
    if (on) hit = true;
  });
  if (!hit) { showPage('today'); return; }
  document.querySelectorAll('.nav a').forEach(a => {
    a.classList.toggle('active', a.dataset.page === id);
  });
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', () => showPage(location.hash.slice(1) || 'today'));
document.addEventListener('DOMContentLoaded', () => showPage(location.hash.slice(1) || 'today'));
"""

ICONS = {
    "today": '<circle cx="8" cy="8" r="5.6"/><path d="M8 5v3l2 1.4"/>',
    "topics": '<path d="M8 1.8c2.6 0 4.4 1.9 4.4 4.3 0 1.7-.9 2.6-1.7 3.4-.5.5-.7.9-.7 1.6H6c0-.7-.2-1.1-.7-1.6-.8-.8-1.7-1.7-1.7-3.4C3.6 3.7 5.4 1.8 8 1.8Z"/><path d="M6.3 13.3h3.4"/><path d="M7 14.7h2"/>',
    "projects": '<rect x="2.2" y="3.2" width="11.6" height="9.6" rx="1.6"/><path d="M2.6 6.2h10.8"/><path d="M5.4 6.2v6.6M10.6 6.2v6.6"/>',
    "publish": '<path d="M8 10.4V2.6"/><path d="M5.2 5.4 8 2.6l2.8 2.8"/><path d="M2.8 10.2v2.2c0 .6.5 1.1 1.1 1.1h8.2c.6 0 1.1-.5 1.1-1.1v-2.2"/>',
    "data": '<path d="M2.5 13.5h11"/><path d="M4.6 13.5V8.4"/><path d="M8 13.5V3.6"/><path d="M11.4 13.5V6.2"/>',
    "knowledge": '<path d="M3 3.2h4.4c.6 0 1.1.5 1.1 1.1v8.9c0-.6-.5-1.1-1.1-1.1H3Z"/><path d="M13 3.2H8.6c-.6 0-1.1.5-1.1 1.1v8.9c0-.6.5-1.1 1.1-1.1H13Z"/>',
    "system": '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.8v1.6M8 12.6v1.6M2.2 8h1.6M12.2 8h1.6M4.1 4.1l1.1 1.1M10.8 10.8l1.1 1.1M11.9 4.1l-1.1 1.1M5.2 10.8l-1.1 1.1"/>',
}


def _nav_item(page: str, label: str) -> str:
    icon = ICONS.get(page, "")
    return (f'<a href="#{page}" data-page="{page}">'
            f'<svg viewBox="0 0 16 16" width="15" height="15" fill="none" '
            f'stroke="currentColor" stroke-width="1.35" stroke-linecap="round" '
            f'stroke-linejoin="round">{icon}</svg>{label}</a>')


def render(root: Path, data: dict) -> str:
    logo = _logo_data_uri(root)
    brand_mark = (f'<img src="{logo}" alt="{BRAND["name"]}">' if logo
                  else '<span class="logo-fallback">T</span>')
    favicon = f'<link rel="icon" href="{logo}">' if logo else ""

    nav = f"""
    <div class="nav-group">
      <div class="nav-label">主任务</div>
      <nav class="nav">
        {_nav_item("today", "今日")}
        {_nav_item("topics", "选题")}
        {_nav_item("projects", "制作")}
        {_nav_item("publish", "发布")}
      </nav>
    </div>
    <div class="nav-group">
      <div class="nav-label">资产</div>
      <nav class="nav">
        {_nav_item("data", "复盘")}
        {_nav_item("knowledge", "知识库")}
      </nav>
    </div>
    <div class="nav-group">
      <div class="nav-label">设置</div>
      <nav class="nav">
        {_nav_item("system", "系统")}
      </nav>
    </div>"""

    projects_html = "".join(render_project_card(p) for p in data["projects"]) or (
        '<div class="card empty-card"><p><b>还没找到项目。</b>先跑 '
        '<code>.\\run.cmd init 我的第01期</code></p></div>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{BRAND["name"]} · {BRAND["sub_before"]}{BRAND["sub_accent"]}{BRAND["sub_after"]}</title>
{favicon}
<style>{CSS}</style>
</head>
<body>
<div class="app">
  <aside class="side">
      <div class="brand">
      {brand_mark}
      <div>
        <b>{BRAND["name"]}</b>
        <small>{BRAND["sub_before"]}<span class="ai">{BRAND["sub_accent"]}</span>{BRAND["sub_after"]}</small>
      </div>
    </div>
    {nav}
    <div class="side-foot">
      {_esc(data["root_name"])}<br>
      生成于 {_esc(data["generated_at"])} · 只读
    </div>
  </aside>

  <main class="main">
    <div class="inner">
      <div class="topbar">
        <span class="meta">{len(data["projects"])} 个项目 · {len(data["rows"])} 条数据 · {data["vault"]["total"]} 篇笔记</span>
        <span class="meta">刷新 <code>.\\run.cmd board</code></span>
      </div>

      <section class="page" id="page-today">{render_today(data)}</section>
      <section class="page" id="page-topics">{render_topics(data)}</section>
      <section class="page" id="page-projects">
        <div class="page-head"><h1>制作</h1><p class="sub">一条片子一个目录。切点表是唯一必须你亲眼看的东西。</p></div>
        {projects_html}
      </section>
      <section class="page" id="page-publish">{render_publish(data)}</section>
      <section class="page" id="page-data">{render_data(data)}</section>
      <section class="page" id="page-knowledge">{render_knowledge(data)}</section>
      <section class="page" id="page-system">{render_system(data)}</section>
    </div>
  </main>
</div>
<script>{JS}</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="board", description="生成只读工作台")
    parser.add_argument("--root", default=".", help="扫描根目录，默认当前目录")
    parser.add_argument("--out", default="", help="输出文件名，默认 工作台.html")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    data = collect(root)
    out = Path(args.out) if args.out else root / OUT_NAME
    out.write_text(render(root, data), encoding="utf-8")

    print(f"已生成：{out}")
    print(f"  {len(data['projects'])} 个项目，{len(data['rows'])} 条数据，"
          f"{data['vault']['total']} 篇笔记")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
