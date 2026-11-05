# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""情报 → 选题候选 → 选题卡。

    python intel.py fetch  <项目目录> [--days 14]
    python intel.py add    <项目目录> --title "..." [--url ...] [--source ...]
    python intel.py rank   <项目目录> [--top 12]
    python intel.py pick   <项目目录> <序号>

分工写在前面，免得用错：

- **脚本负责**：抓（RSS/Atom）、去重、按可解释的五项打分排序、把候选落成选题卡骨架。
- **脚本不判断选题好不好。**"反常识""值不值得做"是语义判断，机器算不出来，
  硬算就是假装有结论。所以 `rank` 输出的是**排序建议**，`pick` 生成的四栏是**空的**。
- **谁来判断**：人和专家 agent。他们看候选表，决定做 / 不做 / 换角度。

落盘位置：
- 情报库在工作区根 `intel/raw.jsonl`（账号级，跨期累积，第二期不用重抓）
- 选题候选和选题卡在项目里 `00-intel/`（期级，跟着这一期走）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import profile as profile_mod  # noqa: E402

SOURCES_FILE = "intel-sources.json"
INTEL_DIR = "intel"
RAW_FILE = "raw.jsonl"
PROJECT_INTEL_DIR = "00-intel"
CANDIDATE_MD = "选题候选.md"
TOPICS_JSON = "topics.json"
TOPIC_CARD = "选题卡.md"

UA = "Mozilla/5.0 (compatible; tiger-video-expert/1.0)"
TIMEOUT = 12

# 权重是可以改的，但只有这五项。加第六项之前先想清楚它和已有项是否重复。
WEIGHTS = {
    "track": 0.35,    # 赛道匹配：命中身份卡关键词
    "heat": 0.25,     # 热度：几个源在说同一件事
    "fresh": 0.20,    # 时效：多新
    "verify": 0.10,   # 可验证：有没有具体数字和出处
    "shoot": 0.10,    # 可拍：有没有第一人称经历，能不能今天动手
}

HEAT_WORD = ("实测", "亲测", "上手", "试了", "踩坑", "复盘", "我们", "我")
HOW_WORD = ("怎么做", "如何", "流程", "步骤", "方法", "拆解", "清单")
ORG_HINT = re.compile(r"《[^》]{2,30}》|[\u4e00-\u9fa5]{2,10}(公司|团队|大学|研究院|实验室|平台|官方)")
NUM_HINT = re.compile(r"\d+(\.\d+)?\s*(%|万|亿|千|倍|元|美元|天|小时|分钟|秒|人|条|个)?")

DEFAULT_SOURCES = {
    "_说明": "往里加 RSS/Atom 源。type 只支持 rss（Atom 也算）。抓不到会逐条报错，不影响其他源。",
    "sources": [
        {"name": "示例源（换成你要跟的）", "type": "rss", "url": "https://example.com/feed", "enabled": False}
    ],
}


# ---------------------------------------------------------------- 落盘位置

def find_workspace(start: Path) -> Path:
    """从项目目录往上找身份卡，找到哪层哪层就是工作区。"""
    cur = start.resolve()
    for _ in range(4):
        if (cur / profile_mod.PROFILE_FILE).exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()


def workspace_of(project: Path) -> Path:
    """身份卡该待的地方。

    找不到就退回项目的父目录，而不是项目自己——身份卡是账号级的，
    塞进某一期里，第二期就得重填一遍。
    """
    project = project.resolve()
    ws = find_workspace(project)
    return project.parent if ws == project else ws


def raw_path(ws: Path) -> Path:
    return ws / INTEL_DIR / RAW_FILE


def load_raw(ws: Path) -> list[dict]:
    p = raw_path(ws)
    if not p.exists():
        return []
    items = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def save_raw(ws: Path, items: list[dict]) -> None:
    p = raw_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def item_id(title: str, url: str) -> str:
    key = (url or title).strip().lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 抓取

def _fetch_url(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def parse_feed(xml_bytes: bytes, source: str) -> list[dict]:
    """RSS 2.0 和 Atom 都吃。解析不出来就抛，不吞。"""
    root = ET.fromstring(xml_bytes)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    out: list[dict] = []

    entries = root.findall(".//item")
    if entries:
        for e in entries:
            out.append({
                "title": (e.findtext("title") or "").strip(),
                "url": (e.findtext("link") or "").strip(),
                "summary": _strip_html(e.findtext("description") or ""),
                "published": _parse_date(e.findtext("pubDate") or ""),
                "source": source,
            })
        return [x for x in out if x["title"]]

    entries = root.findall(".//atom:entry", ns) or root.findall(".//entry")
    for e in entries:
        link = ""
        le = e.find("atom:link", ns) or e.find("link")
        if le is not None:
            link = le.get("href") or (le.text or "")
        out.append({
            "title": (e.findtext("atom:title", default="", namespaces=ns) or e.findtext("title") or "").strip(),
            "url": link.strip(),
            "summary": _strip_html(
                e.findtext("atom:summary", default="", namespaces=ns)
                or e.findtext("summary") or ""
            ),
            "published": _parse_date(
                e.findtext("atom:published", default="", namespaces=ns)
                or e.findtext("published") or ""
            ),
            "source": source,
        })
    return [x for x in out if x["title"]]


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:400]


def _parse_date(text: str) -> str:
    """统一成 YYYY-MM-DD。认不出就返回空串，不猜。"""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(text).date().isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:len(datetime.now().strftime(fmt)) + 6], fmt).date().isoformat()
        except Exception:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    return m.group(0) if m else ""


# ---------------------------------------------------------------- fetch

def cmd_fetch(args) -> int:
    project = Path(args.dir).resolve()
    ws = workspace_of(project)

    sp = ws / SOURCES_FILE
    if not sp.exists():
        sp.write_text(json.dumps(DEFAULT_SOURCES, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"没有 {sp}，已生成模板。把示例源换成你要跟的，再跑一次。", file=sys.stderr)
        print(f"  {sp}", file=sys.stderr)
        return 1

    conf = json.loads(sp.read_text(encoding="utf-8"))
    sources = [s for s in (conf.get("sources") or []) if s.get("url")]
    if not sources:
        print(f"{sp} 里没有可用源（sources 为空，或都是 enabled: false）", file=sys.stderr)
        return 1

    items = load_raw(ws)
    seen_url = {i.get("url") for i in items if i.get("url")}
    seen_id = {i.get("id") for i in items}
    cutoff = None
    if args.days > 0:
        cutoff = date.today().toordinal() - args.days

    added = skipped = failed = 0
    for src in sources:
        if src.get("enabled") is False:
            print(f"  - {src.get('name') or src['url']}：enabled=false，跳过")
            continue
        name = src.get("name") or src["url"]
        try:
            body = _fetch_url(src["url"])
            got = parse_feed(body, name)
        except (urllib.error.URLError, ET.ParseError, OSError) as e:
            print(f"  ✗ {name}：{type(e).__name__}: {e}", file=sys.stderr)
            failed += 1
            continue

        n = 0
        for g in got:
            if cutoff and g.get("published"):
                try:
                    if date.fromisoformat(g["published"]).toordinal() < cutoff:
                        skipped += 1
                        continue
                except ValueError:
                    pass
            gid = item_id(g["title"], g.get("url", ""))
            if gid in seen_id or (g.get("url") and g["url"] in seen_url):
                skipped += 1
                continue
            g["id"] = gid
            g["fetched"] = datetime.now().isoformat(timespec="seconds")
            items.append(g)
            seen_id.add(gid)
            if g.get("url"):
                seen_url.add(g["url"])
            n += 1
            added += 1
        print(f"  ✓ {name}：{len(got)} 条，新增 {n}")

    save_raw(ws, items)
    print()
    print(f"情报库: {raw_path(ws)}（累计 {len(items)} 条）")
    print(f"本次新增 {added}｜重复/过期 {skipped}｜失败源 {failed}")
    if failed:
        print("有源抓失败。失败就报出来，不假装抓到了——换个源或检查网络，别指望它下次自己好。", file=sys.stderr)
    if not added:
        print("这次没新增。要么源没更新，要么抓到的东西都已经在库里了。")
    print("\n下一步：python scripts/pipeline.py intel rank .")
    return 0 if added or not failed else 1


# ---------------------------------------------------------------- add

def cmd_add(args) -> int:
    project = Path(args.dir).resolve()
    ws = workspace_of(project)
    items = load_raw(ws)

    gid = item_id(args.title, args.url)
    if any(i.get("id") == gid for i in items):
        print(f"已有同名条目，没重复加：{args.title}")
        return 0

    items.append({
        "id": gid,
        "title": args.title.strip(),
        "url": (args.url or "").strip(),
        "summary": (args.summary or "").strip()[:400],
        "published": (args.published or date.today().isoformat()).strip(),
        "source": (args.source or "手工").strip(),
        "fetched": datetime.now().isoformat(timespec="seconds"),
    })
    save_raw(ws, items)
    print(f"已加入情报库（共 {len(items)} 条）：{args.title}")
    return 0


# ---------------------------------------------------------------- 打分

def _bigrams(text: str) -> set[str]:
    text = re.sub(r"[\s\W_]+", "", text or "")
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _cluster(items: list[dict], threshold: float = 0.5) -> tuple[list[int], list[int]]:
    """标题二元组相似就算同一件事。返回 (每条的簇大小, 每条的簇根)。

    用两个指标取其一，因为标题的"同一件事"有两种写法：
    - Jaccard：两句话差不多长、措辞接近
    - 包含度：央视写「剪映新版把草稿改成加密」，快讯写「剪映新版草稿加密」——
      后者几乎是前者的子集，Jaccard 只有 0.42 会被漏掉，包含度 0.71 才抓得住。
    短标题（少于 4 个二元组）不参与包含度判断，否则「AI 剪辑」这种词会吸走一切。
    """
    CONT_MIN = 0.65
    MIN_GRAMS = 4

    grams = [_bigrams(i.get("title", "")) for i in items]
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = grams[i], grams[j]
            if not a or not b:
                continue
            inter = len(a & b)
            jac = inter / len(a | b)
            cont = inter / min(len(a), len(b))
            same = jac >= threshold or (min(len(a), len(b)) >= MIN_GRAMS and cont >= CONT_MIN)
            if same:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri

    roots = [find(i) for i in range(len(items))]
    counts: dict[int, int] = {}
    for r in roots:
        counts[r] = counts.get(r, 0) + 1
    return [counts[r] for r in roots], roots


def collapse(items: list[dict], roots: list[int], sizes: list[int]) -> list[dict]:
    """同一件事只留一条，把来源合并成列表。

    不合并的话，一个热点被三家媒体报道，候选表前三行就是同一条内容，
    人还得自己看出来这是重复的——那是把去重的工作推给用户。
    """
    groups: dict[int, list[int]] = {}
    for i, r in enumerate(roots):
        groups.setdefault(r, []).append(i)

    out = []
    for r, idxs in groups.items():
        rep = max(
            idxs,
            key=lambda i: (len(items[i].get("summary") or ""), items[i].get("published") or ""),
        )
        merged = dict(items[rep])
        merged["sources"] = sorted({
            items[i].get("source") for i in idxs if items[i].get("source")
        }) or [items[rep].get("source", "")]
        merged["cluster"] = sizes[rep]
        merged["also"] = [
            {"source": items[i].get("source"), "url": items[i].get("url")}
            for i in idxs if i != rep
        ]
        merged["_text"] = " ".join(
            f"{items[i].get('title','')} {items[i].get('summary','')}" for i in idxs
        )[:1200]
        out.append(merged)
    return out


def _terms(track: list[str]) -> list[tuple[str, str]]:
    """把赛道关键词拆成可匹配的词，返回 [(匹配词, 归属的关键词)]。

    为什么拆：身份卡里人会写「AI 工具」这种短语，但标题里只会出现「AI」。
    只按整串匹配的话，命中率是 0，排序会退化成按热度排——那就等于没有筛。
    只按空格和常见分隔符拆，不猜中文词边界：拆错比不拆更糟。
    """
    out: list[tuple[str, str]] = []
    for kw in track:
        kw = (kw or "").strip()
        if not kw:
            continue
        low = kw.lower()
        out.append((low, kw))
        for part in re.split(r"[\s、,，/·]+", low):
            part = part.strip()
            if len(part) >= 2 and part != low:
                out.append((part, kw))
    return out


def score_items(items: list[dict], profile: dict | None) -> list[dict]:
    sizes, roots = _cluster(items)
    items = collapse(items, roots, sizes)
    track = [t for t in ((profile or {}).get("track") or []) if t]
    terms = _terms(track)
    today = date.today()

    scored = []
    for it in items:
        text = it.pop("_text", "") or f"{it.get('title','')} {it.get('summary','')}"
        low = text.lower()

        # 按"命中了几个不同的赛道关键词"算分，同一个词的多种写法不重复计分
        matched_kw = {kw for term, kw in terms if term in low}
        track_s = min(1.0, len(matched_kw) / 2) if track else 0.0

        heat_s = min(1.0, (it.get("cluster", 1) - 1) / 2)

        pub = it.get("published") or ""
        if pub:
            try:
                age = (today - date.fromisoformat(pub)).days
                fresh_s = 1.0 if age <= 3 else max(0.0, 1 - (age - 3) / 18)
            except ValueError:
                fresh_s = 0.5
        else:
            fresh_s = 0.5

        verify_s = (0.5 if NUM_HINT.search(text) else 0.0) + (0.5 if ORG_HINT.search(text) else 0.0)
        shoot_s = (0.5 if any(w in text for w in HEAT_WORD) else 0.0) + \
                  (0.5 if any(w in text for w in HOW_WORD) else 0.0)

        total = (WEIGHTS["track"] * track_s + WEIGHTS["heat"] * heat_s +
                 WEIGHTS["fresh"] * fresh_s + WEIGHTS["verify"] * verify_s +
                 WEIGHTS["shoot"] * shoot_s)

        scored.append({
            **it,
            "score": round(total, 3),
            "parts": {
                "track": round(track_s, 2),
                "heat": round(heat_s, 2),
                "fresh": round(fresh_s, 2),
                "verify": round(verify_s, 2),
                "shoot": round(shoot_s, 2),
            },
            "matched": sorted(matched_kw),
        })

    scored.sort(key=lambda x: (-x["score"], x.get("published", ""), x.get("title", "")))
    return scored


def _why(it: dict) -> str:
    bits = []
    if it["matched"]:
        bits.append(f"命中 {'、'.join(it['matched'])}")
    if it["cluster"] > 1:
        bits.append(f"{it['cluster']} 个源在说同一件事")
    if it["parts"]["verify"] >= 0.5:
        bits.append("有数字或具体出处")
    if it["parts"]["shoot"] >= 0.5:
        bits.append("是第一人称经历，能自己讲")
    if not bits:
        bits.append("没命中赛道关键词，也没热度，只因为新才排上来")
    return "；".join(bits)


# ---------------------------------------------------------------- rank

def cmd_rank(args) -> int:
    project = Path(args.dir).resolve()
    ws = workspace_of(project)
    items = load_raw(ws)
    if not items:
        print(f"情报库是空的（{raw_path(ws)}）。先跑 intel fetch 或 intel add。", file=sys.stderr)
        return 1

    profile = profile_mod.load(ws)
    if profile is None:
        print("提醒：没找到身份卡，赛道匹配这条没参与打分，排序会偏向「新」和「热」。",
              file=sys.stderr)
        print("      先跑 pipeline.py profile init，排序才有靶心。", file=sys.stderr)

    scored = score_items(items, profile)
    top = scored[: args.top]

    out_dir = project / PROJECT_INTEL_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / TOPICS_JSON).write_text(
        json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    track = "、".join((profile or {}).get("track") or []) or "（未设置身份卡）"
    goal = (profile or {}).get("goal") or "—"
    who = ((profile or {}).get("audience") or {}).get("who") or "—"
    blind = bool((profile or {}).get("track")) and not any(
        it["parts"]["track"] > 0 for it in scored
    )
    if blind:
        print(f"警告：一条都没命中赛道关键词「{track}」。", file=sys.stderr)
        print("      这份排序等于只按热度和时效排的，跟你的账号没关系。", file=sys.stderr)
        print("      改法：track 换成标题里真会出现的词，或换几家更贴赛道的源。", file=sys.stderr)

    lines = [
        f"# 选题候选 · {date.today().isoformat()}",
        "",
        f"身份：{track}｜目标：{goal}",
        f"说给谁听：{who}",
        "",
        f"情报库 {len(items)} 条，取前 {len(top)} 条。",
        "",
    ]
    if blind:
        lines += [
            f"> ⚠️ **一条都没命中赛道关键词「{track}」。**",
            "> 下面的排序只反映了热度和时效，跟你的账号没有关系。",
            "> 先改 `profile.json` 的 `track`（换成标题里真会出现的词），或换几家更贴赛道的源，再重新 rank。",
            "",
        ]
    lines += [
        "> **这张表是排序建议，不是结论。** 打分只用了五个能算的客观特征：",
        f"> 赛道匹配 {int(WEIGHTS['track']*100)}%、热度 {int(WEIGHTS['heat']*100)}%、"
        f"时效 {int(WEIGHTS['fresh']*100)}%、可验证 {int(WEIGHTS['verify']*100)}%、"
        f"可拍 {int(WEIGHTS['shoot']*100)}%。",
        "> 「反常识」「值不值得做」机器算不出来，要人过四关：具体性 / 可验证 / 反常识 / 拍摄可行性。",
        "",
        "| # | 得分 | 标题 | 来源 | 赛道 | 热度 | 时效 | 可验证 | 可拍 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n, it in enumerate(top, 1):
        p = it["parts"]
        title = (it.get("title") or "").replace("|", "／")[:46]
        srcs = "、".join(it.get("sources") or [it.get("source", "")])
        lines.append(
            f"| {n} | {it['score']:.2f} | {title} | {srcs} | "
            f"{p['track']:.1f} | {p['heat']:.1f} | {p['fresh']:.1f} | {p['verify']:.1f} | {p['shoot']:.1f} |"
        )

    lines += ["", "## 为什么这么排", ""]
    for n, it in enumerate(top[:5], 1):
        lines.append(f"**{n}. {it.get('title','')}**")
        lines.append("")
        lines.append(f"- {_why(it)}")
        if it.get("url"):
            lines.append(f"- <{it['url']}>")
        for a in it.get("also") or []:
            if a.get("url"):
                lines.append(f"- 同一件事还有人报：{a.get('source','')} <{a['url']}>")
        lines.append("")

    lines += [
        "## 下一步",
        "",
        "```bash",
        f"python scripts/pipeline.py topic pick . 1     # 选第 1 条落成选题卡",
        "```",
        "",
        "落出来的选题卡四栏是空的，得自己填。填不满就写「不做」——这一条留着，下周不会重想一遍。",
        "",
    ]
    (out_dir / CANDIDATE_MD).write_text("\n".join(lines), encoding="utf-8")

    print(f"候选表: {out_dir / CANDIDATE_MD}")
    print(f"机器可读: {out_dir / TOPICS_JSON}")
    print()
    print("前 5 条：")
    for n, it in enumerate(top[:5], 1):
        srcs = "、".join(it.get("sources") or [it.get("source", "")])
        print(f"  {n}. {it['score']:.2f}  {it.get('title','')[:40]}  ({srcs})")
    print()
    print("打分只是排序建议。四关还得人过，别拿分数当结论。")
    print("下一步：python scripts/pipeline.py topic pick . <序号>")
    return 0


# ---------------------------------------------------------------- pick

def cmd_pick(args) -> int:
    project = Path(args.dir).resolve()
    out_dir = project / PROJECT_INTEL_DIR
    tj = out_dir / TOPICS_JSON
    if not tj.exists():
        print(f"找不到 {tj}，先跑 pipeline.py intel rank .", file=sys.stderr)
        return 1

    topics = json.loads(tj.read_text(encoding="utf-8"))
    if not topics:
        print("候选是空的。", file=sys.stderr)
        return 1
    if not 1 <= args.n <= len(topics):
        print(f"序号要在 1-{len(topics)} 之间，现在是 {args.n}", file=sys.stderr)
        return 1

    it = topics[args.n - 1]
    p = it["parts"]
    srcs = "、".join(it.get("sources") or [it.get("source", "")])
    card = [
        "---",
        "type: 选题卡",
        f"date: {date.today().isoformat()}",
        f"来源: 情报候选 #{args.n}",
        f"得分: {it['score']:.2f}",
        f"项目目录: {project.name}",
        "结论: （待定：做 / 不做 / 换角度）",
        "---",
        "",
        f"# {it.get('title','')}",
        "",
        "## 情报依据",
        "",
        f"- 标题：{it.get('title','')}",
        f"- 来源：{srcs}（{it.get('cluster',1)} 个源在说同一件事）",
        f"- 发布日期：{it.get('published','') or '未标注'}",
        f"- 链接：{it.get('url','') or '无'}",
        f"- 打分：赛道 {p['track']:.1f}｜热度 {p['heat']:.1f}｜时效 {p['fresh']:.1f}"
        f"｜可验证 {p['verify']:.1f}｜可拍 {p['shoot']:.1f}",
        f"- 摘要：{it.get('summary','')[:200] or '无'}",
        "",
        "> 以上是**线索**，不是选题。下面四栏填完才算有选题；填不满就写「不做」。",
        "",
        "## 一句话选题",
        "",
        "## 目标观众",
        "",
        "- 职业：",
        "- 处境：",
        "- 正在为这件事发愁的是：",
        "",
        "## 反常识点",
        "",
        "- 大家以为：",
        "- 实际上是：",
        "- 让人「咦」的那一下是：",
        "",
        "## 素材清单",
        "",
        "- 已有：",
        "- 缺：",
        "",
        "## 结论",
        "",
        "**做 / 不做 / 换角度**：",
        "",
        "理由：",
        "",
    ]
    (out_dir / TOPIC_CARD).write_text("\n".join(card), encoding="utf-8")

    pj = project / "project.json"
    if pj.exists():
        data = json.loads(pj.read_text(encoding="utf-8"))
        data["topic"] = {
            "title": it.get("title", ""),
            "source": srcs,
            "url": it.get("url", ""),
            "score": it["score"],
            "picked": date.today().isoformat(),
        }
        pj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"选题卡: {out_dir / TOPIC_CARD}")
    print(f"标题: {it.get('title','')}")
    print()
    print("四栏还是空的——这是故意的。")
    print("把它填满，结论写「做 / 不做 / 换角度」。填不满就写不做，别硬凑理由。")
    print()
    print("定完之后：")
    print("  1. 写脚本，放到 01-script/script.md")
    print("  2. 素材放 02-raw/main.mp4")
    print("  3. python scripts/pipeline.py ingest .")
    return 0


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intel", description="情报采集与选题排序（只给建议，不做结论）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="抓 RSS/Atom 到情报库")
    p.add_argument("dir", help="项目目录")
    p.add_argument("--days", type=int, default=14, help="只要最近几天的，0 表示不限")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("add", help="手工/agent 喂入一条情报")
    p.add_argument("dir", help="项目目录")
    p.add_argument("--title", required=True)
    p.add_argument("--url", default="")
    p.add_argument("--summary", default="")
    p.add_argument("--source", default="手工")
    p.add_argument("--published", default="")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("rank", help="按五项特征打分排序")
    p.add_argument("dir", help="项目目录")
    p.add_argument("--top", type=int, default=12)
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("pick", help="选定一条，落成选题卡")
    p.add_argument("dir", help="项目目录")
    p.add_argument("n", type=int, help="候选序号，从 1 开始")
    p.set_defaults(func=cmd_pick)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
