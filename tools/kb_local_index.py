# -*- coding: utf-8 -*-
"""
本地离线知识库索引引擎（水云间设计工作台）
==========================================
完全替代远程 MaxKB 在「检索」环节的角色：**不联网、不依赖 embedding 模型**。

- 扫描 knowledge-base 下全部 markdown（case-studies / 各智能体分库 agents/*/cases /
  fengshui / prompts），并可选纳入专家包的系统提示词（真实方法论知识）。
- 中文二元组（bigram）分词 + BM25 打分，复用与 app.py 相同的检索思路，纯标准库。
- 索引落盘为单个 JSON，工作台启动时懒加载（后台构建），搜索时直接命中。
- 自动过滤「模型拒答」垃圾卡（内容为空 / 以"很抱歉…请提供"开头），保证检索质量。

用法（命令行构建 / 校验）：
    python kb_index.py --kb <knowledge-base 目录> --out <kb-index.json>
    python kb_index.py --kb <dir> --extra <专家agents目录> --out <kb-index.json>

作为库：
    from kb_index import build_index, search_index
    build_index(kb_dir, out_path, extra_dirs=[...])
    hits = search_index(out_path, "北欧 客厅 木质", k=8)
"""
import os
import re
import sys
import json
import math
import argparse
from datetime import datetime

# ---------------------------------------------------------------------------
# 分词（与 app.py 的 _bigrams 保持一致：中文/混合文本二元组）
# ---------------------------------------------------------------------------
_CJK = r"一-鿿"  # CJK Unified Ideographs U+4E00–U+9FFF


def bigrams(text):
    """中文/混合文本的二元组切分，用于轻量检索打分（无需 embedding 模型）。"""
    toks = re.findall(r"[\w" + _CJK + r"]{2,}", text or "")
    out = set(toks)
    for t in toks:
        if len(t) >= 3:
            for i in range(len(t) - 1):
                out.add(t[i:i + 2])
    return out


# ---------------------------------------------------------------------------
# 文档清洗 / 质量过滤
# ---------------------------------------------------------------------------
_JUNK_MARKERS = ("很抱歉", "抱歉，", "抱歉!", "无法处理", "信息为空", "信息不足",
                 "素材信息", "无法提取", "提取和概括", "请提供具体", "请提供相关",
                 "i cannot", "i'm sorry", "sorry,", "cannot process", "unable to")


def strip_frontmatter(text):
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def is_junk(text):
    """判断一张卡是否为「模型拒答 / 空卡」，应在建索引时剔除。"""
    t = (text or "").strip()
    if len(t) < 40:
        return True
    head = t[:200].lower()
    if any(m in head for m in _JUNK_MARKERS):
        return True
    return False


def doc_title(text, fallback):
    m = re.search(r'title:\s*"([^"]+)"', text)
    if m:
        return m.group(1).strip()
    m = re.search(r"^#\s+(.+)$", text, re.M)
    if m:
        return m.group(1).strip()
    return fallback


def parse_agents_field(text):
    m = re.search(r'agents:\s*\[([^\]]*)\]', text or "")
    if not m:
        return []
    return [t.strip().strip('"\'') for t in re.findall(r'[A-Za-z_]+', m.group(1))]


# ---------------------------------------------------------------------------
# 索引构建
# ---------------------------------------------------------------------------
def _collect_sources(kb_dir, extra_dirs=None):
    roots = [
        os.path.join(kb_dir, "case-studies"),
        os.path.join(kb_dir, "fengshui"),
        os.path.join(kb_dir, "prompts"),
    ]
    agents_dir = os.path.join(kb_dir, "agents")
    if os.path.isdir(agents_dir):
        for a in sorted(os.listdir(agents_dir)):
            c = os.path.join(agents_dir, a, "cases")
            if os.path.isdir(c):
                roots.append(c)
    if extra_dirs:
        roots += list(extra_dirs)
    existing = [r for r in roots if os.path.isdir(r)]
    return existing


def build_index(kb_dir, out_path, extra_dirs=None, verbose=False):
    """扫描并构建 BM25 索引，写入 out_path（单文件 JSON）。返回统计字典。"""
    sources = _collect_sources(kb_dir, extra_dirs)
    if verbose:
        print("[kb_index] 扫描根目录：")
        for s in sources:
            print("  -", s)

    docs = []  # {id, path(rel), abs, title, agents, length}
    skipped = 0
    for root in sources:
        for dirpath, _, fnames in os.walk(root):
            for fn in fnames:
                if not fn.lower().endswith(".md"):
                    continue
                if fn.lower() == "readme.md":
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    with open(fp, encoding="utf-8") as f:
                        raw = f.read()
                except Exception:
                    continue
                body = strip_frontmatter(raw)
                if is_junk(body):
                    skipped += 1
                    continue
                rel = os.path.relpath(fp, kb_dir)
                docs.append({
                    "id": len(docs),
                    "path": rel,
                    "abs": fp,
                    "title": doc_title(raw, fn),
                    "agents": parse_agents_field(raw),
                    "length": len(body),
                    "_body": body,  # 临时，用于建倒排，写完即丢
                })

    N = len(docs)
    # 倒排索引：term -> {doc_id: tf}
    inv = {}
    for d in docs:
        tf = {}
        for t in bigrams(d["_body"]):
            tf[t] = tf.get(t, 0) + 1
        d["tf"] = tf
        for t, c in tf.items():
            inv.setdefault(t, {})[d["id"]] = c

    avgdl = (sum(d["length"] for d in docs) / N) if N else 0

    index = {
        "version": 2,
        "built": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kb_dir": os.path.abspath(kb_dir),
        "sources": sources,
        "n_docs": N,
        "avgdl": avgdl,
        "docs": [
            {"id": d["id"], "path": d["path"], "title": d["title"],
             "agents": d["agents"], "length": d["length"], "abs": d["abs"]}
            for d in docs
        ],
        "inv": inv,  # term -> {doc_id: tf}
    }
    # 释放临时正文
    for d in docs:
        d.pop("_body", None)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(out_path)
    stat = {
        "n_docs": N,
        "skipped_junk": skipped,
        "n_terms": len(inv),
        "index_bytes": size,
        "out_path": out_path,
        "built": index["built"],
    }
    if verbose:
        print("[kb_index] 构建完成：%d 篇有效文档，跳过 %d 张垃圾卡，%d 个词项，索引 %.2f MB"
              % (N, skipped, len(inv), size / 1024 / 1024))
    return stat


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
def _load(out_path):
    with open(out_path, encoding="utf-8") as f:
        return json.load(f)


def _snippet(body, qterms, width=160):
    """在文档正文中截取含查询词的片段。"""
    low = body.lower()
    pos = -1
    for qt in qterms:
        q = qt.lower()
        i = low.find(q)
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        return body[:width].strip()
    start = max(0, pos - width // 3)
    return body[start:start + width].strip()


def search_index(out_path, query, k=8, agent=None):
    """BM25 检索。返回命中列表（按评分降序）：
    [{id, path, title, agents, score, snippet}]。agent 给定时仅返回该智能体分库文档。"""
    if not query or not query.strip():
        return []
    index = _load(out_path)
    N = index["n_docs"]
    if N == 0:
        return []
    avgdl = index["avgdl"]
    inv = index["inv"]
    docs = {d["id"]: d for d in index["docs"]}
    qterms = bigrams(query)
    if not qterms:
        return []

    scores = {}
    for qt in qterms:
        postings = inv.get(qt)
        if not postings:
            continue
        df = len(postings)
        idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
        for did_s, f in postings.items():
            did = int(did_s)
            dl = docs[did]["length"]
            denom = f + 1.2 * (1 - 0.75 + 0.75 * dl / avgdl) if avgdl else f + 1.2
            s = idf * (f * 2.2) / denom
            scores[did] = scores.get(did, 0) + s

    ranked = sorted(scores.items(), key=lambda x: -x[1])[:k]
    hits = []
    for did, sc in ranked:
        d = docs[did]
        if agent:
            # 仅返回该智能体分库（路径形如 agents/<agent>/cases/...）文档
            if not d["path"].replace("\\", "/").startswith("agents/%s/cases" % agent):
                continue
        # 重新读正文取片段（索引不存全文）
        try:
            with open(d["abs"], encoding="utf-8") as f:
                body = strip_frontmatter(f.read())
        except Exception:
            body = ""
            # 兜底：用 kb_dir + path 重建（兼容旧索引缺 abs 字段）
            try:
                with open(os.path.join(index.get("kb_dir", ""), d["path"]),
                          encoding="utf-8") as f2:
                    body = strip_frontmatter(f2.read())
            except Exception:
                body = ""
        hits.append({
            "id": did,
            "path": d["path"],
            "title": d["title"],
            "agents": d["agents"],
            "score": round(sc, 3),
            "snippet": _snippet(body, qterms),
        })
    return hits


def main():
    ap = argparse.ArgumentParser(description="水云间本地知识库索引构建器（离线 / 零网络）")
    ap.add_argument("--kb", required=True, help="knowledge-base 目录")
    ap.add_argument("--out", required=True, help="输出索引 json 路径")
    ap.add_argument("--extra", nargs="*", default=[], help="额外纳入的目录（如专家包 agents）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    stat = build_index(args.kb, args.out, extra_dirs=args.extra or None, verbose=not args.quiet)
    if args.quiet:
        print(json.dumps(stat, ensure_ascii=False))


if __name__ == "__main__":
    main()
