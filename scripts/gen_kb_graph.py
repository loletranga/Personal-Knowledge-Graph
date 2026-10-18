#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_kb_graph.py — 从 Markdown 目录生成文件式知识图谱。

用法:
    python3 gen_kb_graph.py [目录] [--output graph] [--ignore PATTERN1,PATTERN2] [--no-html]

扫描目录下所有 .md 文件（跳过隐藏目录、.kbignore 与输出目录），解析
frontmatter 元数据与 [[wikilink]]，全量重建以下生成物:

    <目录>/<output>/INDEX.md       决策登记簿（active / superseded 文档一览）
    <目录>/<output>/GRAPH.md       按领域分组的图谱邻接表
    <目录>/<output>/kb-graph.json  机器可读全图（nodes + edges）
    <目录>/<output>/report.md      健康报告（缺元数据 / 断链 / 演进链异常）
    <目录>/<output>/kb-graph.html  单文件可视化页面（瑞士档案风格）

纯 Python 标准库，幂等，可随时重跑。图谱事实源永远是文档本身，本目录
生成物删掉即可完整重建。
"""

import argparse
import datetime
import fnmatch
import json
import re
import sys
from pathlib import Path

DEFAULT_IGNORES = {".venv", "venv", "node_modules", "__pycache__", ".git"}
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
MDLINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
KV_RE = re.compile(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$")


def parse_frontmatter(text):
    """宽容解析 YAML 围栏 frontmatter，返回 (meta_dict, body)。"""
    meta, body = {}, text
    m = FM_RE.match(text)
    if not m:
        return meta, body
    body = text[m.end():]
    current = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        item = ITEM_RE.match(line)
        if item and current is not None:
            val = item.group(1).strip().strip('"').strip("'")
            if not isinstance(meta.get(current), list):
                prev = meta.get(current)
                meta[current] = [prev] if prev else []
            meta[current].append(val)
            continue
        kv = KV_RE.match(line)
        if kv:
            key, val = kv.group(1).lower(), kv.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                meta[key] = [v.strip().strip('"').strip("'") for v in inner.split(",")] if inner else []
                current = key
            elif val == "":
                meta[key] = []
                current = key
            else:
                meta[key] = val.strip('"').strip("'")
                current = key
    return meta, body


def norm_date(s):
    """尽量归一化为 YYYY-MM-DD；解析失败原样返回（排序时沉底）。"""
    if not s:
        return ""
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m", "%Y/%m"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.match(r"^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return s


def aslist(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def split_rel(value):
    """'[[name]]: note' / '[[name]]' / 'name: note' -> (name, note)"""
    v = str(value).strip()
    m = WIKILINK_RE.search(v)
    if m:
        note = v[m.end():].strip().lstrip(":：").strip()
        return m.group(1).strip(), note
    if ":" in v:
        name, note = v.split(":", 1)
        return name.strip(), note.strip()
    return v, ""


def first_heading(body):
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("##"):
            return s[2:].strip()
    return ""


def brief_line(body):
    """正文第一行非标题文字，作为无 conclusion 文档的简要含义。"""
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"^[-*>\s]+", "", s)
        s = WIKILINK_RE.sub(lambda m: (m.group(2) or m.group(1)), s)
        s = MDLINK_RE.sub(r"\1", s)
        s = re.sub(r"[`*_]", "", s).strip()
        if not s:
            continue
        return (s[:79] + "…") if len(s) > 80 else s
    return ""


def load_kbignore(root):
    pats = []
    ig = root / ".kbignore"
    if ig.exists():
        for line in ig.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def iter_md_files(root, outdir, extra_ignores):
    ignores = DEFAULT_IGNORES | set(extra_ignores) | {outdir}
    pats = load_kbignore(root)
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        parts = rel.parts[:-1]
        if any(part in ignores or part.startswith(".") for part in parts):
            continue
        posix = rel.as_posix()
        if any(fnmatch.fnmatch(posix, pat) or fnmatch.fnmatch(part, pat)
               for part in parts for pat in pats) or \
           any(fnmatch.fnmatch(posix, pat) for pat in pats):
            continue
        yield p


def build(root, outdir, extra_ignores):
    nodes, bodies, metas = {}, {}, {}
    for p in iter_md_files(root, outdir, extra_ignores):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_text(encoding="utf-8", errors="replace")
        if text.startswith("\ufeff"):
            text = text[1:]
        meta, body = parse_frontmatter(text)
        domains = aslist(meta.get("domain"))
        tags = aslist(meta.get("tags"))
        status = str(meta.get("status", "reference")).strip() or "reference"
        nodes[rel] = {
            "id": rel,
            "title": first_heading(body) or p.stem,
            "date": norm_date(meta.get("date")),
            "status": status,
            "domain": domains,
            "project": str(meta.get("project", "")).strip(),
            "conclusion": str(meta.get("conclusion", "")).strip(),
            "brief": brief_line(body),
            "tags": tags,
            "has_frontmatter": bool(meta),
        }
        bodies[rel], metas[rel] = body, meta

    by_stem = {}
    for n in nodes.values():
        by_stem.setdefault(Path(n["id"]).stem, []).append(n["id"])

    def resolve(name):
        name = str(name).strip().strip('"').strip("'")
        if name.endswith(".md"):
            name = name[:-3]
        if not name:
            return None, "空链接"
        if name in nodes:
            return name, "ok"
        noext = [n for n in nodes if n[:-3] == name]
        if len(noext) == 1:
            return noext[0], "ok"
        hits = by_stem.get(name.rsplit("/", 1)[-1], [])
        if len(hits) == 1:
            return hits[0], "ok"
        if len(hits) > 1:
            return None, "歧义: " + "; ".join(hits[:4])
        return None, "目标不存在"

    edges, broken = [], []
    seen_edge = set()

    def add_edge(src, raw, etype):
        name, note = split_rel(raw)
        tgt, how = resolve(name)
        if tgt:
            key = (src, tgt, etype)
            if key not in seen_edge:
                seen_edge.add(key)
                edges.append({"from": src, "to": tgt, "type": etype, "note": note})
        else:
            broken.append((src, etype, name, how))

    for rel, meta in metas.items():
        for raw in aslist(meta.get("supersedes")):
            add_edge(rel, raw, "supersedes")
        for raw in aslist(meta.get("related")):
            add_edge(rel, raw, "related")
        seen_link = set()
        for m in WIKILINK_RE.finditer(bodies[rel]):
            name = m.group(1).strip()
            if name in seen_link:
                continue
            seen_link.add(name)
            add_edge(rel, name, "references")

    return nodes, edges, broken


def current_chain(nodes, edges):
    """id -> 沿 supersedes 链找现行版；返回 (superseded_by, current_of)"""
    superseded_by = {}
    for e in edges:
        if e["type"] == "supersedes":
            superseded_by.setdefault(e["to"], []).append(e["from"])

    def current_of(nid):
        seen = set()
        while nid in superseded_by and nid not in seen:
            seen.add(nid)
            nid = max(superseded_by[nid], key=lambda x: nodes[x]["date"])
        return nid

    return superseded_by, current_of


def find_cycles(edges):
    graph = {}
    for e in edges:
        if e["type"] == "supersedes":
            graph.setdefault(e["from"], []).append(e["to"])
    cycles, seen = [], set()

    def dfs(nid, path):
        for nxt in graph.get(nid, []):
            if nxt in path:
                cycles.append(path[path.index(nxt):] + [nxt])
            elif nxt not in seen:
                dfs(nxt, path + [nxt])
        seen.add(nid)

    for start in list(graph):
        if start not in seen:
            dfs(start, [start])
    return cycles


def esc(s):
    return str(s).replace("|", "/").replace("\n", " ")


def link(nid, nodes):
    return "[[%s|%s]]" % (nid, esc(nodes[nid]["title"]) if nodes[nid]["title"] else nid)


def sort_key(n):
    d = n["date"] if re.match(r"^\d{4}-\d{2}-\d{2}$", n["date"] or "") else "0000"
    return (d, n["title"])


def render_index(nodes, edges, current_of):
    lines = ["# 决策登记簿", "",
             "> 生成物，勿手改（由 gen_kb_graph.py 全量重建）。回忆检索时首读本表。",
             ""]
    docs = [n for n in nodes.values() if n["status"] in ("active", "superseded")]
    docs.sort(key=sort_key, reverse=True)
    if not docs:
        lines.append("（暂无 active / superseded 决策文档）")
        return "\n".join(lines) + "\n"
    lines += ["| 日期 | 文档 | 一句话结论 | 状态 | 现行版 |",
              "|---|---|---|---|---|"]
    for n in docs:
        cur = current_of(n["id"])
        cur_cell = "—" if cur == n["id"] else link(cur, nodes)
        lines.append("| %s | %s | %s | %s | %s |" % (
            esc(n["date"]) or "—", link(n["id"], nodes),
            esc(n["conclusion"]) or "—", n["status"], cur_cell))
    return "\n".join(lines) + "\n"


def render_graph(nodes, edges, superseded_by):
    groups = {}
    for n in nodes.values():
        for d in (n["domain"] or ["未分类"]):
            groups.setdefault(d, []).append(n)
    lines = ["# 知识图谱（按领域）", "",
             "> 生成物，勿手改。顺 supersedes/related 链深读原文。", ""]
    for d in sorted(groups):
        ns = sorted(groups[d], key=sort_key, reverse=True)
        lines.append("## %s（%d 篇）" % (d, len(ns)))
        for n in ns:
            nid = n["id"]
            lines.append("")
            lines.append("- %s %s%s — %s" % (
                esc(n["date"]) or "日期缺失",
                link(nid, nodes),
                "" if n["status"] == "active" else " [%s]" % n["status"],
                esc(n["conclusion"]) or "（无结论）"))
            ups = [e["to"] for e in edges if e["from"] == nid and e["type"] == "supersedes"]
            downs = superseded_by.get(nid, [])
            rels = [(e["to"], e["note"]) for e in edges if e["from"] == nid and e["type"] == "related"]
            refs = [e["to"] for e in edges if e["from"] == nid and e["type"] == "references"]
            if ups:
                lines.append("  - 取代: " + "、".join(link(t, nodes) for t in ups))
            if downs:
                lines.append("  - 被取代: " + "、".join(link(t, nodes) for t in downs))
            if rels:
                lines.append("  - 相关: " + "、".join(
                    link(t, nodes) + (("（%s）" % esc(note)) if note else "") for t, note in rels))
            if refs:
                lines.append("  - 引用: " + "、".join(link(t, nodes) for t in refs))
    return "\n".join(lines) + "\n"


def render_report(root, nodes, edges, broken, outdir):
    nf = [n for n in nodes.values() if not n["has_frontmatter"]]
    miss_date = [n for n in nodes.values() if n["status"] != "reference" and not n["date"]]
    miss_conc = [n for n in nodes.values() if n["status"] != "reference" and not n["conclusion"]]
    orphan_super = [n for n in nodes.values()
                    if n["status"] == "superseded"
                    and not any(e["to"] == n["id"] for e in edges if e["type"] == "supersedes")]
    cycles = find_cycles(edges)
    counts = {}
    for e in edges:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    statuses = {}
    for n in nodes.values():
        statuses[n["status"]] = statuses.get(n["status"], 0) + 1

    L = ["# 知识库健康报告", "",
         "生成时间: %s  |  根目录: `%s`  |  输出: `%s/`" % (
             datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), root, outdir),
         ""]
    L += ["## 总览", "",
          "- 文档 %d 篇；有 frontmatter %d 篇（%.0f%%）；边 %d 条（演进 %d / 语义 %d / 引用 %d）" % (
              len(nodes), len(nodes) - len(nf),
              100.0 * (len(nodes) - len(nf)) / len(nodes) if nodes else 0,
              len(edges), counts.get("supersedes", 0), counts.get("related", 0),
              counts.get("references", 0)),
          "- 状态分布: " + ("、".join("%s %d" % kv for kv in sorted(statuses.items())) or "无"), ""]
    L += ["## 缺 date（active/superseded 必填）", ""]
    L += ["- %s" % n["id"] for n in sorted(miss_date, key=sort_key)] or ["- （无）"]
    L += ["", "## 缺 conclusion（active/superseded 必填）", ""]
    L += ["- %s" % n["id"] for n in sorted(miss_conc, key=sort_key)] or ["- （无）"]
    L += ["", "## 断链 / 指向不存在的目标", ""]
    L += ["- %s → [%s] %s（%s）" % (s, t, esc(name), how) for s, t, name, how in broken] or ["- （无）"]
    L += ["", "## superseded 但无人取代它（可能漏写新文档的 supersedes）", ""]
    L += ["- %s" % n["id"] for n in orphan_super] or ["- （无）"]
    L += ["", "## 演进链成环（supersedes 互相指向）", ""]
    L += ["- " + " → ".join(c) for c in cycles] or ["- （无）"]
    L += ["", "## 缺 frontmatter（未入元数据层）", ""]
    L += ["- %s" % n["id"] for n in sorted(nf, key=sort_key)] or ["- （无）"]
    return "\n".join(L) + "\n"


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>知识图谱 · KNOWLEDGE ATLAS</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{
  --paper:#F4F2EB; --card:#FFFFFF; --ink:#16150F; --muted:#716E63; --hair:#DCD8CB;
  --red:#E63329; --blue:#2545D3; --green:#0E8A5F; --yellow:#F2B90D; --gray:#A9A496;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,"Courier New",monospace;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:flex;flex-direction:column;
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Helvetica Neue","Microsoft YaHei",sans-serif;
  color:var(--ink);background:var(--paper)}
.mono{font-family:var(--mono)}

/* ---------- 头部 ---------- */
header{display:flex;align-items:flex-end;gap:18px;padding:16px 24px 13px;border-bottom:2px solid var(--ink)}
.mark{width:22px;height:22px;background:var(--red);flex:none;margin-bottom:8px}
h1{margin:0;font-size:27px;font-weight:800;letter-spacing:.01em;line-height:1.1}
h1 .latin{font-family:var(--mono);font-size:11px;font-weight:400;color:var(--red);letter-spacing:.26em;margin-left:12px;vertical-align:4px}
#stats{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.06em;margin-top:6px;text-transform:uppercase}
#headright{margin-left:auto;font-family:var(--mono);font-size:10.5px;color:var(--muted);line-height:1.75;text-align:right;max-width:380px;overflow:hidden}

/* ---------- 工具栏 ---------- */
#toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 24px;border-bottom:1.5px solid var(--ink);background:var(--card)}
#search{width:272px;padding:6px 12px;border:1.5px solid var(--ink);border-radius:0;font-size:13px;outline:none;background:var(--card);font-family:inherit}
#search:focus{box-shadow:3px 3px 0 var(--red)}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 11px;border:1.5px solid var(--ink);border-radius:0;font-family:var(--mono);font-size:11px;letter-spacing:.05em;cursor:pointer;user-select:none;background:var(--card);text-transform:uppercase}
.chip.on{background:var(--ink);color:var(--paper)}
.chip .dot{width:8px;height:8px;flex:none;display:inline-block}
.tab{margin-left:auto;display:flex;border:1.5px solid var(--ink)}
.tab button{padding:5px 20px;border:0;background:var(--card);font-size:12.5px;font-weight:700;cursor:pointer;font-family:inherit;letter-spacing:.08em}
.tab button.on{background:var(--red);color:#fff}

/* ---------- 主区 ---------- */
main{display:flex;flex:1;min-height:0}
#graphView{flex:1;position:relative;min-width:0;overflow:hidden;
  background-image:radial-gradient(var(--hair) 1px,transparent 1px);background-size:26px 26px}
#wires{position:absolute;inset:0;width:100%;height:100%}
#nodeLayer{position:absolute;inset:0}

/* ---------- 节点档案卡片 ---------- */
.node{position:absolute;left:0;top:0;width:186px;will-change:transform}
.node.s-reference{width:168px}
.node .in{transform:translate(-50%,-50%);background:var(--card);border:1.5px solid var(--ink);
  padding:8px 10px 9px;cursor:pointer;box-shadow:3px 3px 0 rgba(22,21,15,.13);transition:box-shadow .15s}
.node .in:hover{box-shadow:5px 5px 0 rgba(22,21,15,.2)}
.n-meta{display:flex;justify-content:space-between;align-items:center;gap:8px;
  font-family:var(--mono);font-size:9.5px;letter-spacing:.07em;color:var(--muted);text-transform:uppercase}
.node.s-active .n-date{color:var(--red);font-weight:700}
.n-dom{display:flex;align-items:center;gap:4px;min-width:0}
.n-dom .dot{width:7px;height:7px;flex:none;display:inline-block}
.n-dom .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.n-title{font-size:13.5px;font-weight:700;line-height:1.3;margin-top:4px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.n-sum{font-size:11px;color:var(--muted);line-height:1.45;margin-top:4px;padding-left:7px;
  border-left:2px solid var(--red);
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.node.s-reference .n-sum{border-left-color:var(--hair)}
.node.s-superseded .in{background:#ECE9DF;border-style:dashed;border-color:var(--gray);box-shadow:none}
.node.s-superseded .n-title{color:#8B8778;font-weight:600}
.node.s-superseded .n-sum{border-left-color:var(--gray);color:#9A9585}
.node.s-superseded .in:hover{box-shadow:none}
.node.sel .in{border-color:var(--red);box-shadow:5px 5px 0 var(--red)}
.node.dim{opacity:.13}
#graphView.far .n-sum,#graphView.far .n-meta{display:none}
#graphView.far .node .in{padding:6px 8px}

/* ---------- 图例 ---------- */
#legend{position:absolute;left:16px;bottom:14px;font-family:var(--mono);font-size:10.5px;color:var(--muted);
  background:var(--card);border:1.5px solid var(--ink);padding:8px 12px;line-height:2;max-width:370px;
  box-shadow:3px 3px 0 rgba(22,21,15,.12)}
#legend i{display:inline-block;width:20px;height:0;border-top:2.5px solid;margin-right:5px;vertical-align:3px}
#legend .sq{display:inline-block;width:9px;height:9px;border:1.5px solid var(--ink);margin-right:5px;vertical-align:-1px}
#legend .sqg{border-style:dashed;border-color:var(--gray);background:#ECE9DF}

/* ---------- 详情面板 ---------- */
#detail{width:362px;border-left:1.5px solid var(--ink);background:var(--card);overflow:auto;padding:20px;display:none;flex-shrink:0}
#detail .kicker{font-family:var(--mono);font-size:10px;letter-spacing:.26em;color:var(--red);margin-bottom:7px}
#detail h3{margin:0 0 12px;font-size:17px;line-height:1.4;font-weight:800}
.d-row{display:flex;gap:10px;font-size:12.5px;margin:6px 0;line-height:1.5}
.d-lab{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase;width:82px;flex:none;padding-top:3px}
.d-concl{margin:13px 0;padding:10px 12px;border:1.5px solid var(--ink);background:#FBF3D9;
  box-shadow:3px 3px 0 rgba(22,21,15,.12);font-size:13px;line-height:1.6}
.d-concl .lab{font-family:var(--mono);font-size:9.5px;letter-spacing:.22em;color:var(--red);display:block;margin-bottom:4px}
.badge{display:inline-block;padding:1px 8px;border:1px solid;font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase}
.badge.active{color:var(--green);border-color:var(--green);background:#E7F4EE}
.badge.superseded{color:#75716A;border-color:var(--gray);background:#F0EEE6}
.badge.reference{color:var(--blue);border-color:var(--blue);background:#E9EDFB}
.jump{color:var(--ink);cursor:pointer;text-decoration:underline;text-underline-offset:3px;text-decoration-thickness:1px}
.jump:hover{color:var(--red)}
.dim{color:var(--muted)}
#detail .src{margin-top:16px;font-family:var(--mono);font-size:12px}
#detail .close{float:right;cursor:pointer;color:var(--muted);font-size:19px;line-height:1}
#detail .close:hover{color:var(--red)}

/* ---------- 清单视图 ---------- */
#tableView{flex:1;overflow:auto;padding:22px 26px;background:var(--paper)}
#tableView .kicker{font-family:var(--mono);font-size:10px;letter-spacing:.26em;color:var(--red);margin:14px 0 5px}
#tableView h3{font-size:17px;font-weight:800;margin:0 0 10px}
#tableView table{border-collapse:collapse;width:100%;font-size:13px;background:var(--card);border:1.5px solid var(--ink)}
#tableView th{font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
  font-weight:400;text-align:left;padding:8px 12px;border-bottom:1.5px solid var(--ink);background:#FAF8F1;position:sticky;top:0}
#tableView td{padding:8px 12px;border-bottom:1px solid var(--hair);vertical-align:top}
#tableView tr:hover td{background:#FBFAF4}
#tableView .empty{color:var(--muted);font-family:var(--mono);font-size:11px;padding:14px}

footer{padding:8px 24px;font-family:var(--mono);font-size:10.5px;color:var(--muted);border-top:1.5px solid var(--ink);
  letter-spacing:.05em;display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap}
</style>
</head>
<body>
<header>
  <div class="mark"></div>
  <div>
    <h1>知识图谱<span class="latin">KNOWLEDGE&nbsp;ATLAS</span></h1>
    <div id="stats"></div>
  </div>
  <div id="headright"></div>
</header>
<div id="toolbar">
  <input id="search" placeholder="检索 标题 / 结论 / 文件名 …">
  <span id="statusFilters"></span>
  <span id="domainFilters"></span>
  <span class="tab"><button id="btnGraph" class="on">图谱</button><button id="btnTable">清单</button></span>
</div>
<main>
  <div id="graphView">
    <svg id="wires"><defs>
      <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
        <path d="M0 0L10 5L0 10z" fill="#E63329"/>
      </marker>
    </defs><g id="vp"><g id="edgeLayer"></g></g></svg>
    <div id="nodeLayer"></div>
    <div id="legend">
      <div><i style="border-color:#E63329"></i>取代（演进）　<i style="border-color:#2545D3"></i>相关　<i style="border-color:#C7C2B2;border-top-style:dashed"></i>引用</div>
      <div><span class="sq"></span>active / reference　<span class="sq sqg"></span>superseded（已取代）　卡片随关联增多而增大</div>
      <div>滚轮缩放 · 拖空白平移 · 拖卡片调整 · 单击看档案 · 双击打开源文件</div>
    </div>
  </div>
  <div id="tableView" style="display:none"></div>
  <aside id="detail"></aside>
</main>
<footer><span id="foot"></span><span>REBUILD · python3 gen_kb_graph.py</span></footer>
<script>const KB_DATA = __KB_DATA__;</script>
<script>
const D=KB_DATA, nodes=D.nodes, edges=D.edges, NS="http://www.w3.org/2000/svg";
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const byId={};
nodes.forEach((n,i)=>{n.i=i;n.deg=0;n.fx=null;n.hide=false;
  n.x=600+470*Math.cos(i*2.399);n.y=400+380*Math.sin(i*2.399);n.vx=0;n.vy=0;byId[n.id]=n;});
edges.forEach(e=>{const a=byId[e.from],b=byId[e.to];if(a&&b){a.deg++;b.deg++;}});
const domains=[...new Set(nodes.flatMap(n=>n.dom&&n.dom.length?n.dom:["未分类"]))];
const PALETTE=["#E63329","#2545D3","#0E8A5F","#F2B90D","#7C3AED","#DB2777","#0891B2","#F97316","#65A30D","#64748B"];
const colOf=d=>PALETTE[((domains.indexOf(d)%PALETTE.length)+PALETTE.length)%PALETTE.length];
const primDom=n=>(n.dom&&n.dom.length)?n.dom[0]:"未分类";

document.getElementById("stats").textContent=
  `docs ${nodes.length} · edges ${edges.length} · active ${nodes.filter(n=>n.s==="active").length} · superseded ${nodes.filter(n=>n.s==="superseded").length} · reference ${nodes.filter(n=>n.s==="reference").length} · domains ${domains.length}`;
document.getElementById("headright").innerHTML=
  `GEN ${esc(D.generated_at)}<br>ROOT ${esc(D.root)}`;
document.getElementById("foot").textContent=
  `GENERATED ${D.generated_at} · ${nodes.length} FILES · ${edges.length} LINKS · 生成物勿手改，重跑脚本即刷新`;

/* ---------- 边（SVG 层） ---------- */
const svg=document.getElementById("wires"),vp=document.getElementById("vp");
const eg=document.getElementById("edgeLayer"),nodeLayer=document.getElementById("nodeLayer");
const edgeEls=[],nodeEls=[];
edges.forEach(e=>{const a=byId[e.from],b=byId[e.to];if(!a||!b)return;
  const ln=document.createElementNS(NS,"line");
  if(e.type==="supersedes"){ln.setAttribute("stroke","#E63329");ln.setAttribute("stroke-width",2);ln.setAttribute("marker-end","url(#arr)");}
  else if(e.type==="related"){ln.setAttribute("stroke","#2545D3");ln.setAttribute("stroke-width",1.7);}
  else{ln.setAttribute("stroke","#C7C2B2");ln.setAttribute("stroke-width",1.2);ln.setAttribute("stroke-dasharray","5 4");}
  ln.dataset.a=a.i;ln.dataset.b=b.i;
  eg.appendChild(ln);edgeEls.push(ln);});

/* ---------- 节点（档案卡片层） ---------- */
nodes.forEach(n=>{
  const el=document.createElement("div");
  el.className="node s-"+n.s;el.dataset.i=n.i;
  const doms=(n.dom&&n.dom.length)?n.dom:["未分类"],dom=doms[0];
  const domHtml=n.s==="superseded"
    ?'<span style="color:var(--gray)">已取代</span>'
    :`<span class="dot" style="background:${colOf(dom)}"></span><span class="t">${esc(dom)}${doms.length>1?" +"+(doms.length-1):""}</span>`;
  el.innerHTML=`<div class="in">
    <div class="n-meta"><span class="n-date">${esc(n.d)||"NO DATE"}</span><span class="n-dom">${domHtml}</span></div>
    <div class="n-title">${esc(n.t||n.id)}</div>
    ${n.b?`<div class="n-sum">${esc(n.b)}</div>`:""}
  </div>`;
  nodeLayer.appendChild(el);nodeEls.push(el);});

/* ---------- 力导向（为大卡片调校） ---------- */
let alpha=1;
function step(){
  const N=nodes.length;
  for(let i=0;i<N;i++){const a=nodes[i];
    for(let j=i+1;j<N;j++){const b=nodes[j];
      let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy;
      if(d2>102400)continue;if(d2<1)d2=1;
      const d=Math.sqrt(d2);
      let f=26000*alpha/d2;
      if(d<210)f+=(210-d)*0.028;         /* 卡片最小间距 */
      const fx=dx/d*f,fy=dy/d*f;
      a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy;}}
  edges.forEach(e=>{const a=byId[e.from],b=byId[e.to];if(!a||!b)return;
    const dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1;
    const rest=e.type==="references"?255:(e.type==="related"?220:200),f=(d-rest)*0.004;
    const fx=dx/d*f,fy=dy/d*f;
    a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy;});
  nodes.forEach(n=>{
    n.vx+=(600-n.x)*0.0008;n.vy+=(400-n.y)*0.0008;
    if(n.fx!=null){n.x=n.fx;n.y=n.fy;n.vx=n.vy=0;}
    else{n.vx*=0.85;n.vy*=0.85;n.x+=n.vx;n.y+=n.vy;}});
}
alpha=1;
for(let k=0;k<300;k++){step();alpha*=0.972;}

/* ---------- 渲染与视图变换 ---------- */
let scale=1,tx=0,ty=0;
const graphView=document.getElementById("graphView");
function render(){
  edgeEls.forEach(ln=>{const a=nodes[ln.dataset.a],b=nodes[ln.dataset.b];
    if(a.hide||b.hide){ln.style.display="none";return;}ln.style.display="";
    ln.setAttribute("x1",a.x);ln.setAttribute("y1",a.y);
    ln.setAttribute("x2",b.x);ln.setAttribute("y2",b.y);});
  nodeEls.forEach((el,i)=>{const n=nodes[i];
    el.style.display=n.hide?"none":"";
    el.style.transform="translate("+(n.x*scale+tx)+"px,"+(n.y*scale+ty)+"px)";});
  vp.setAttribute("transform","translate("+tx+","+ty+") scale("+scale+")");
  graphView.classList.toggle("far",scale<0.45);
}
function fit(){
  const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y);
  const x0=Math.min(...xs)-160,x1=Math.max(...xs)+160,y0=Math.min(...ys)-120,y1=Math.max(...ys)+120;
  const r=graphView.getBoundingClientRect();
  scale=Math.min(r.width/(x1-x0),r.height/(y1-y0),1.35);
  tx=(r.width-(x0+x1)*scale)/2;ty=(r.height-(y0+y1)*scale)/2;render();
}
svg.addEventListener("wheel",ev=>{ev.preventDefault();
  const k=ev.deltaY<0?1.12:1/1.12,ns=Math.min(3.2,Math.max(0.06,scale*k)),r=ns/scale;
  const b=graphView.getBoundingClientRect(),mx=ev.clientX-b.left,my=ev.clientY-b.top;
  tx=mx-(mx-tx)*r;ty=my-(my-ty)*r;scale=ns;render();},{passive:false});
let drag=null,downXY=null;
graphView.addEventListener("mousedown",ev=>{
  const el=ev.target.closest?ev.target.closest(".node"):null;
  downXY=[ev.clientX,ev.clientY];
  if(el){drag={n:nodes[+el.dataset.i]};}
  else if(ev.target.closest("#legend"))return;
  else drag={pan:true,sx:ev.clientX,sy:ev.clientY,tx0:tx,ty0:ty};
  ev.preventDefault();});
window.addEventListener("mousemove",ev=>{if(!drag)return;
  const b=graphView.getBoundingClientRect();
  if(drag.pan){tx=drag.tx0+ev.clientX-drag.sx;ty=drag.ty0+ev.clientY-drag.sy;render();}
  else{drag.n.x=(ev.clientX-b.left-tx)/scale;drag.n.y=(ev.clientY-b.top-ty)/scale;
    drag.n.fx=drag.n.x;drag.n.fy=drag.n.y;alpha=Math.max(alpha,0.3);render();}});
window.addEventListener("mouseup",ev=>{
  if(!drag)return;
  const moved=downXY&&Math.hypot(ev.clientX-downXY[0],ev.clientY-downXY[1])>4;
  if(drag.n){delete drag.n.fx;delete drag.n.fy;if(!moved)select(drag.n);}
  else if(!moved)clearSel();
  drag=null;});
graphView.addEventListener("dblclick",ev=>{
  const el=ev.target.closest?ev.target.closest(".node"):null;
  if(el){const n=nodes[+el.dataset.i];window.open("../"+encodeURIComponent(n.id),"__blank");}});

/* ---------- 选择与档案详情 ---------- */
const detail=document.getElementById("detail");
let selected=null;
function neighborsOf(n){const s=new Set();
  edges.forEach(e=>{if(e.from===n.id)s.add(e.to);if(e.to===n.id)s.add(e.from);});return s;}
function select(n){selected=n;
  const nb=neighborsOf(n);
  nodes.forEach(m=>{const el=nodeEls[m.i];
    el.classList.toggle("sel",m===n);el.classList.toggle("dim",m!==n&&!nb.has(m.id));});
  edgeEls.forEach(ln=>{const a=nodes[ln.dataset.a],b=nodes[ln.dataset.b];
    ln.style.opacity=(a===n||b===n)?1:0.05;});
  showDetail(n);}
function clearSel(){selected=null;
  nodeEls.forEach(el=>el.classList.remove("sel","dim"));
  edgeEls.forEach(l=>l.style.opacity="");
  detail.style.display="none";}
function centerOn(n){const b=graphView.getBoundingClientRect();
  tx=b.width/2-n.x*scale;ty=b.height/2-n.y*scale;render();}
function jumps(sel){document.querySelectorAll(sel).forEach(a=>a.addEventListener("click",ev=>{
  ev.preventDefault();const n=byId[a.dataset.id];if(!n)return;
  document.getElementById("btnGraph").click();select(n);centerOn(n);}));}
function relHtml(n,type,label){
  const out=[];
  edges.forEach(e=>{
    if(e.type!==type)return;
    if(e.from===n.id)out.push([e.to,e.note]);
    else if(type!=="supersedes"&&e.to===n.id)out.push([e.from,e.note]);});
  if(!out.length)return"";
  return`<div class="d-row"><span class="d-lab">${label}</span><span>`+
    out.map(([id,note])=>`<a class="jump" data-id="${esc(id)}">${esc(byId[id]?byId[id].t:id)}</a>`+
      (note?`<span class="dim">（${esc(note)}）</span>`:"")).join("、")+"</span></div>";}
function showDetail(n){
  detail.style.display="block";
  const cur=(n.cur&&n.cur!==n.id&&byId[n.cur])?
    `<div class="d-row"><span class="d-lab">current 现行版</span><span><a class="jump" data-id="${esc(n.cur)}">${esc(byId[n.cur].t)}</a></span></div>`:"";
  detail.innerHTML=`<span class="close" title="关闭">×</span>
    <div class="kicker">NODE FILE · 节点档案</div>
    <h3>${esc(n.t||n.id)}</h3>
    <div class="d-row"><span class="d-lab">file 文件</span><span class="dim mono" style="font-size:11.5px">${esc(n.id)}</span></div>
    <div class="d-row"><span class="d-lab">date 日期</span><span class="mono" style="font-size:12px">${esc(n.d)||"—"}</span></div>
    <div class="d-row"><span class="d-lab">status 状态</span><span class="badge ${n.s}">${n.s}</span></div>
    <div class="d-row"><span class="d-lab">domain 领域</span><span>${esc((n.dom&&n.dom.length?n.dom:["未分类"]).join("、"))}</span></div>
    ${n.proj?`<div class="d-row"><span class="d-lab">project 项目</span><span>${esc(n.proj)}</span></div>`:""}
    ${n.c?`<div class="d-concl"><span class="lab">CONCLUSION · 结论</span>${esc(n.c)}</div>`:""}
    ${cur}${relHtml(n,"supersedes","supersedes 取代")}
    ${relHtml(n,"related","related 相关")}${relHtml(n,"references","references 引用")}
    <div class="src"><a class="jump" href="../${encodeURIComponent(n.id)}" target="_blank">打开源文件 OPEN ↗</a></div>`;
  detail.querySelector(".close").onclick=clearSel;jumps("#detail .jump");}

/* ---------- 筛选与搜索 ---------- */
const state={q:"",domains:new Set(domains),statuses:new Set(["active","superseded","reference"])};
const sf=document.getElementById("statusFilters"),df=document.getElementById("domainFilters");
["active","superseded","reference"].forEach(s=>{
  const c=document.createElement("span");c.className="chip on";c.textContent=s;
  c.onclick=()=>{if(state.statuses.has(s))state.statuses.delete(s);else state.statuses.add(s);
    c.classList.toggle("on");applyFilter();};sf.appendChild(c);});
domains.forEach(d=>{
  const c=document.createElement("span");c.className="chip on";
  c.innerHTML=`<span class="dot" style="background:${colOf(d)}"></span>${esc(d)}`;
  c.onclick=()=>{if(state.domains.has(d))state.domains.delete(d);else state.domains.add(d);
    c.classList.toggle("on");applyFilter();};df.appendChild(c);});
document.getElementById("search").addEventListener("input",ev=>{
  state.q=ev.target.value.trim().toLowerCase();applyFilter();});
function applyFilter(){
  nodes.forEach(n=>{
    const hay=((n.t||"")+" "+(n.c||"")+" "+(n.b||"")+" "+n.id).toLowerCase();
    const domOK=(n.dom&&n.dom.length?n.dom:["未分类"]).some(d=>state.domains.has(d));
    n.hide=!(state.statuses.has(n.s)&&domOK&&(state.q===""||hay.includes(state.q)));
    nodeEls[n.i].classList.toggle("dim",!n.hide&&!!selected&&selected!==n&&!neighborsOf(selected).has(n.id));
    if(n.hide&&selected===n)clearSel();});
  if(selected&&selected.hide)clearSel();
  render();if(document.getElementById("tableView").style.display!=="none")renderTable();}

/* ---------- 清单视图 ---------- */
const tableView=document.getElementById("tableView");
function rows(list,withCur){
  return list.map(n=>`<tr><td class="mono" style="font-size:11.5px">${esc(n.d)||"—"}</td>
    <td><a class="jump" data-id="${esc(n.id)}">${esc(n.t||n.id)}</a></td>
    <td>${esc((n.dom&&n.dom.length?n.dom:["未分类"]).join("、"))}</td>
    <td><span class="badge ${n.s}">${n.s}</span></td>
    <td>${esc(n.c)||`<span class="dim">${esc(n.b||"")}</span>`}</td>
    ${withCur?`<td>${n.cur&&n.cur!==n.id?`<a class="jump" data-id="${esc(n.cur)}">${esc(byId[n.cur].t)}</a>`:'<span class="dim">—</span>'}</td>`:""}</tr>`).join("");}
function renderTable(){
  const vis=nodes.filter(n=>!n.hide);
  const byDate=(a,b)=>String(b.d||"").localeCompare(String(a.d||""));
  const dec=vis.filter(n=>n.s!=="reference").sort(byDate);
  const all=vis.slice().sort(byDate);
  const head=withCur=>`<tr><th>date 日期</th><th>doc 文档</th><th>domain 领域</th><th>status 状态</th><th>conclusion 一句话结论</th>${withCur?"<th>current 现行版</th>":""}</tr>`;
  tableView.innerHTML=
    `<div class="kicker">01 — DECISIONS</div><h3>决策登记簿（${dec.length} 篇 · 回忆检索首读）</h3>`+
    (dec.length?`<table>${head(true)}${rows(dec,true)}</table>`:'<div class="empty">NO DECISION DOCS</div>')+
    `<div class="kicker">02 — ALL FILES</div><h3>全部文档（${all.length} 篇）</h3>`+
    `<table>${head(false)}${rows(all,false)}</table>`;
  jumps("#tableView .jump");}
const btnG=document.getElementById("btnGraph"),btnT=document.getElementById("btnTable");
btnG.onclick=()=>{btnG.classList.add("on");btnT.classList.remove("on");
  graphView.style.display="";tableView.style.display="none";};
btnT.onclick=()=>{btnT.classList.add("on");btnG.classList.remove("on");
  graphView.style.display="none";
  tableView.style.display="block";renderTable();};

(function loop(){if(alpha>0.003){step();alpha*=0.99;}render();requestAnimationFrame(loop);})();
fit();
</script>
</body>
</html>
"""


def render_html(nodes, edges, current_of, generated_at, root):
    ns = sorted(nodes.values(), key=sort_key, reverse=True)
    data = {
        "generated_at": generated_at,
        "root": str(root),
        "nodes": [{
            "id": n["id"], "t": n["title"], "d": n["date"], "s": n["status"],
            "dom": n["domain"], "proj": n["project"],
            "c": n["conclusion"], "b": n["conclusion"] or n["brief"],
            "cur": current_of(n["id"]),
        } for n in ns],
        "edges": edges,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__KB_DATA__", payload)


def main():
    ap = argparse.ArgumentParser(description="从 Markdown 目录生成文件式知识图谱")
    ap.add_argument("root", nargs="?", default=".", help="知识库根目录")
    ap.add_argument("--output", default="graph", help="输出子目录名（默认 graph）")
    ap.add_argument("--ignore", default="", help="额外忽略的目录/通配，逗号分隔")
    ap.add_argument("--no-html", action="store_true", help="跳过 kb-graph.html 可视化页面生成")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print("错误: %s 不是目录" % root, file=sys.stderr)
        sys.exit(1)
    extra = [x.strip() for x in args.ignore.split(",") if x.strip()]
    nodes, edges, broken = build(root, args.output, extra)
    superseded_by, current_of = current_chain(nodes, edges)

    out = root / args.output
    out.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (out / "INDEX.md").write_text(render_index(nodes, edges, current_of), encoding="utf-8")
    (out / "GRAPH.md").write_text(render_graph(nodes, edges, superseded_by), encoding="utf-8")
    (out / "report.md").write_text(render_report(root, nodes, edges, broken, args.output), encoding="utf-8")
    (out / "kb-graph.json").write_text(json.dumps({
        "generated_at": generated_at,
        "root": str(root),
        "nodes": sorted(nodes.values(), key=sort_key, reverse=True),
        "edges": edges,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    html_path = ""
    if not args.no_html:
        html_path = out / "kb-graph.html"
        html_path.write_text(
            render_html(nodes, edges, current_of, generated_at, root), encoding="utf-8")

    nf = sum(1 for n in nodes.values() if not n["has_frontmatter"])
    print("完成: %d 篇文档, %d 条边, %d 篇缺 frontmatter" % (len(nodes), len(edges), nf))
    print("输出目录: %s" % out)
    if html_path:
        print("可视化页面: %s（浏览器打开即可查看）" % html_path)


if __name__ == "__main__":
    main()
