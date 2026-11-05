# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 tiger-fde-os
"""数据回填与迭代（M5）。

为什么单独一个脚本：

前面六步都在产出「东西」，这一步只产出「判断」。平台后台的数字抄回来之后，
要回答的只有两个问题——**这种选题还值不值得做**、**下一条该首发哪个平台**。
不做这两件事，前六步就是一次性劳动，不会有复利。

设计上刻意保守：

1. 样本少于 3 条不给结论。三条以下的「中位数」是噪音，照它调权重只会越调越差。
2. 只提「降权建议」，不改任何配置。要不要降权是人决定的。
3. 平台标签与 pack.py 共用一份，避免文案里叫「小红书」、报表里叫「xhs」。

    python scripts/metrics.py log    <项目目录> --platform douyin --views 12000 --completion 42%
    python scripts/metrics.py report <项目目录> [<项目目录> ...]
    python scripts/metrics.py report --all .        # 扫当前目录下所有项目
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

METRICS_DIR = "07-metrics"
CSV_NAME = "metrics.csv"

# 与 docs/产品规划-v1.md 的字段一致，只在最前面加了 project，
# 否则多条片子的数据混在一张表里就分不清是哪一条。
COLUMNS = [
    "project", "date", "platform", "video_id", "title", "topic_type", "angle",
    "duration", "views", "completion_rate", "likes", "comments", "saves",
    "follows", "note",
]

RATE_FIELDS = ("completion_rate",)


def _platform_labels() -> dict[str, str]:
    """平台代号 -> 中文名。优先用 pack.py 的，保证全链路叫法一致。"""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import pack
        return {k: v.get("label", k) for k, v in pack.PLATFORMS.items()}
    except Exception:
        return {"douyin": "抖音", "xhs": "小红书", "shipinhao": "视频号", "x": "X"}


LABELS = _platform_labels()


def parse_rate(raw: str | float | None) -> float | None:
    """把 0.42 / 42 / 42% / "42 %" 统一成 0-1 的小数。

    平台后台显示的是百分数，手抄进来最容易被写成 42。
    这里不猜单位，只按「大于 1 就是百分数」归一，并对越界值报错。
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
    else:
        text = str(raw).strip().replace("％", "%").rstrip("%").strip()
        if not text:
            return None
        try:
            val = float(text)
        except ValueError as exc:
            raise ValueError(f"看不懂的比率: {raw!r}") from exc
    if val > 1:
        val = val / 100.0
    if not (0 <= val <= 1.0001):
        raise ValueError(f"比率超出 0-100%: {raw!r}")
    return round(val, 4)


def parse_num(raw: str | int | float | None) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"看不懂的数字: {raw!r}") from exc


def tidy_num(value):
    """12000.0 写成 12000。CSV 是给人看的，也方便 Excel 当数字算。"""
    if value is None or value == "":
        return ""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def csv_path(project: Path) -> Path:
    return Path(project) / METRICS_DIR / CSV_NAME


def read_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                row = {k: (v or "").strip() for k, v in row.items() if k}
                row["completion_rate"] = parse_rate(row.get("completion_rate"))
                for key in ("views", "likes", "comments", "saves", "follows", "duration"):
                    row[key] = parse_num(row.get(key))
                if row.get("completion_rate") is None and not row.get("views"):
                    continue          # 整行没数，忽略
                rows.append(row)
    return rows


def write_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    # 新文件带 BOM（Excel 双击打开中文才不乱码）；追加时不能带，
    # 否则 BOM 会被写到文件中间，读回来第一列全是乱字符。
    enc = "utf-8-sig" if fresh else "utf-8"
    with path.open("a", encoding=enc, newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        if fresh:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in COLUMNS})


# ---------------------------------------------------------------- log

def cmd_log(args) -> int:
    project = Path(args.dir).resolve()
    if not project.is_dir():
        raise SystemExit(f"项目目录不存在: {project}")
    if not (project / "project.json").exists():
        print(f"[警告] {project} 没有 project.json，仍按项目目录写入", file=sys.stderr)

    rate = parse_rate(args.completion)
    if rate is None and args.views is None:
        raise SystemExit("至少给一个 --views 或 --completion，否则这行没有信息量")

    row = {
        "project": project.name,
        "date": args.date or "",
        "platform": args.platform,
        "video_id": args.video_id or "",
        "title": args.title or "",
        "topic_type": args.topic_type or "",
        "angle": args.angle or "",
        "duration": tidy_num(args.duration),
        "views": tidy_num(args.views),
        "completion_rate": rate if rate is not None else "",
        "likes": tidy_num(args.likes),
        "comments": tidy_num(args.comments),
        "saves": tidy_num(args.saves),
        "follows": tidy_num(args.follows),
        "note": args.note or "",
    }
    out = csv_path(project)
    write_row(out, row)

    label = LABELS.get(args.platform, args.platform)
    shown = f"{rate * 100:.0f}%" if rate is not None else "—"
    views = f"{int(args.views):,}" if args.views is not None else "—"
    print(f"已记录 {label}：完播 {shown}，播放 {views}")
    print(f"写入 {out}")
    return 0


# ---------------------------------------------------------------- report

def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def analyse(rows: list[dict], min_sample: int = 3) -> dict:
    by_platform: dict[str, list[dict]] = defaultdict(list)
    by_type: dict[str, list[dict]] = defaultdict(list)
    by_type_platform: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_title: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        plat = row.get("platform") or "unknown"
        ttype = row.get("topic_type") or "未标注"
        by_platform[plat].append(row)
        by_type[ttype].append(row)
        by_type_platform[(ttype, plat)].append(row)
        if row.get("title"):
            by_title[row["title"]].append(row)

    def rates(items: list[dict]) -> list[float]:
        return [r["completion_rate"] for r in items if r.get("completion_rate") is not None]

    platform_stats = {
        plat: {"count": len(items), "median": _median(rates(items))}
        for plat, items in by_platform.items()
    }
    type_stats = {
        ttype: {"count": len(items), "median": _median(rates(items))}
        for ttype, items in by_type.items()
    }
    cell_stats = {
        f"{ttype}|{plat}": {"count": len(items), "median": _median(rates(items))}
        for (ttype, plat), items in by_type_platform.items()
    }

    # 降权判定：同一「选题类型 × 平台」最近 3 条全部低于此前基线。
    #
    # 两个必须分开的地方：
    # 1. 不能跨平台比。小红书完播天然高于抖音，混在一起算「最近 3 条」，
    #    会把一条正常的小红书数据当成长尾下滑的证据。
    # 2. 基线要排除最近这 3 条。把它们算进中位数，中位数会被一起拉低，
    #    越下滑越触发不了，等于永远不报警。
    downgrade: list[dict] = []
    thin_types: list[str] = []
    for (ttype, plat), items in by_type_platform.items():
        ranked = [r for r in items if r.get("completion_rate") is not None]
        ranked.sort(key=lambda r: r.get("date") or "")
        baseline_rows = ranked[:-min_sample]
        label = f"{ttype} @ {LABELS.get(plat, plat)}"
        if len(baseline_rows) < min_sample:
            thin_types.append(label)
            continue
        base = _median([r["completion_rate"] for r in baseline_rows])
        if base is None:
            continue
        tail = ranked[-min_sample:]
        if all(r["completion_rate"] < base for r in tail):
            downgrade.append({
                "topic_type": ttype,
                "platform": plat,
                "label": label,
                "median": base,
                "recent": [r["completion_rate"] for r in tail],
                "titles": [r.get("title") or (r.get("project") or "") for r in tail],
            })

    # 首发平台：同一个标题在多个平台发过，完播最高的那个。
    first_platform: list[dict] = []
    for title, items in by_title.items():
        plats = {(r.get("platform") or "") for r in items}
        if len(plats) < 2:
            continue
        ranked = [r for r in items if r.get("completion_rate") is not None]
        if len(ranked) < 2:
            continue
        ranked.sort(key=lambda r: r["completion_rate"], reverse=True)
        first_platform.append({
            "title": title,
            "best": ranked[0].get("platform"),
            "best_rate": ranked[0]["completion_rate"],
            "spread": {r.get("platform"): r["completion_rate"] for r in ranked},
        })

    return {
        "total": len(rows),
        "platform_stats": platform_stats,
        "type_stats": type_stats,
        "cell_stats": cell_stats,
        "downgrade": downgrade,
        "thin_types": thin_types,
        "first_platform": first_platform,
    }


def render(report: dict, min_sample: int = 3) -> str:
    lines: list[str] = []
    total = report["total"]
    lines.append(f"共 {total} 条记录")
    if total == 0:
        lines.append("")
        lines.append("还没有数据。用 metrics.py log 记第一条，或者先跑一次 report --all 看看有没有散落的 csv。")
        return "\n".join(lines)

    def pct(v):
        return f"{v * 100:.0f}%" if v is not None else "—"

    lines.append("")
    lines.append("按平台")
    for plat, st in sorted(report["platform_stats"].items(), key=lambda kv: -(kv[1]["median"] or 0)):
        flag = "" if st["count"] >= min_sample else "  （样本不足，中位数仅参考）"
        lines.append(f"  {LABELS.get(plat, plat):<6} {st['count']:>3} 条   完播中位 {pct(st['median'])}{flag}")

    lines.append("")
    lines.append("按选题类型")
    for ttype, st in sorted(report["type_stats"].items(), key=lambda kv: -(kv[1]["median"] or 0)):
        flag = "" if st["count"] >= min_sample else "  （样本不足）"
        lines.append(f"  {ttype:<10} {st['count']:>3} 条   完播中位 {pct(st['median'])}{flag}")

    if report["cell_stats"]:
        lines.append("")
        lines.append("选题类型 × 平台")
        for key, st in sorted(report["cell_stats"].items()):
            ttype, plat = key.split("|", 1)
            if st["count"] < 2:
                continue
            lines.append(f"  {ttype} @ {LABELS.get(plat, plat):<6} {st['count']:>2} 条   中位 {pct(st['median'])}")

    lines.append("")
    if report["downgrade"]:
        lines.append("建议降权")
        for item in report["downgrade"]:
            recent = " / ".join(pct(v) for v in item["recent"])
            lines.append(f"  {item['label']}：最近 {len(item['recent'])} 条 {recent}，"
                         f"全部低于此前基线 {pct(item['median'])}")
            lines.append("    下周选题卡减少这个类型，别直接停——停掉就分不清是选题问题还是执行问题。")
        if report["thin_types"]:
            lines.append(f"  样本不足，暂不判断：{'、'.join(report['thin_types'])}")
    else:
        lines.append("建议降权：暂无。")
        if report["thin_types"]:
            lines.append(f"  样本不足，还判断不了：{'、'.join(report['thin_types'])}")
            lines.append("  每类要 6 条起：前 3 条做基线，后 3 条看是否连续下滑。")
        else:
            lines.append("  最近 3 条没有全部低于历史基线，维持现有权重。")

    lines.append("")
    if report["first_platform"]:
        lines.append("首发平台建议")
        for item in report["first_platform"]:
            spread = "，".join(f"{LABELS.get(p, p)} {pct(v)}" for p, v in item["spread"].items())
            lines.append(f"  {item['title']}")
            lines.append(f"    最高 {LABELS.get(item['best'], item['best'])} {pct(item['best_rate'])}（{spread}）")
    else:
        lines.append("首发平台建议：还没有同一条内容在两个以上平台的数据。")

    return "\n".join(lines)


def cmd_report(args) -> int:
    if args.all:
        root = Path(args.all).resolve()
        paths = sorted(root.glob(f"*/{METRICS_DIR}/{CSV_NAME}"))
        if not paths:
            print(f"{root} 下没找到 */{METRICS_DIR}/{CSV_NAME}")
            return 1
    else:
        if not args.dirs:
            raise SystemExit("给项目目录，或者用 --all .")
        paths = [csv_path(Path(d).resolve()) for d in args.dirs]

    rows = read_rows(paths)
    report = analyse(rows, min_sample=args.min_sample)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report, min_sample=args.min_sample))
    return 0 if report["total"] else 1


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metrics", description="数据回填与迭代：记数、算中位数、给降权与首发建议"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("log", help="记一条平台数据")
    p.add_argument("dir", help="项目目录")
    p.add_argument("--platform", required=True, help="douyin / xhs / shipinhao / x")
    p.add_argument("--views", type=float, default=None)
    p.add_argument("--completion", default=None, help="完播率，0.42 或 42%% 都行")
    p.add_argument("--likes", type=float, default=None)
    p.add_argument("--comments", type=float, default=None)
    p.add_argument("--saves", type=float, default=None)
    p.add_argument("--follows", type=float, default=None)
    p.add_argument("--duration", type=float, default=None, help="成片时长（秒）")
    p.add_argument("--title", default="")
    p.add_argument("--video-id", default="")
    p.add_argument("--topic-type", default="")
    p.add_argument("--angle", default="")
    p.add_argument("--date", default="", help="发布日期，YYYY-MM-DD")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("report", help="聚合分析")
    p.add_argument("dirs", nargs="*", help="项目目录，可给多个")
    p.add_argument("--all", default="", help="扫某个根目录下的所有项目")
    p.add_argument("--min-sample", type=int, default=3, help="少于此数不给结论")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
