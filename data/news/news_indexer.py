#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_indexer.py
===============
把 data/news/knowledge_base.md 的新闻内容批量建索引（sentence-transformers + faiss）

输出（存到 data/news/index/）：
  - news_meta.json        # 每条新闻的元数据（index_id -> {date, category, stock, text, code}）
  - news_vecs.npy         # 归一化后的向量矩阵 (N, dim)
  - news_index.faiss      # faiss 索引（IndexFlatIP）

用法：
  /Users/duguke/.openclaw/workspace/.venv-ml/bin/python \
      /Users/duguke/.openclaw/workspace/data/news/news_indexer.py

  # 只重建索引（不重新嵌入，用已存的向量）
  python news_indexer.py --skip-embed

  # 建完索引后直接搜一条（配合 --skip-embed 避免重新嵌入）
  python news_indexer.py --search "国瓷材料涨价涨停"
  python news_indexer.py --search-only --search "锂矿 天齐锂业"
  python news_indexer.py --search-only --search "涨价" --stock 国瓷材料
"""

import argparse
import json
import os
import re
import sys

import numpy as np

# ⚠️ Intel Mac + torch 2.2.2 多线程时单条 encode 会段错误 (SIGSEGV)。强制单线程。
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
try:
    import torch
    torch.set_num_threads(1)
except Exception:
    pass

import faiss

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "knowledge_base.md")
INDEX_DIR = os.path.join(BASE, "index")
META_PATH = os.path.join(INDEX_DIR, "news_meta.json")
VECS_PATH = os.path.join(INDEX_DIR, "news_vecs.npy")
FAISS_PATH = os.path.join(INDEX_DIR, "news_index.faiss")

MODEL_NAME = "BAAI/bge-small-zh-v1.5"


# ---------------- 解析 markdown ----------------
def parse_kb(path=KB):
    """解析 knowledge_base.md，返回条目列表：[{date, category, stock, code, text}]"""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    entries = []
    current_date = None
    category = None          # "宏观/政策" | "关注股公告" | "个股新闻"
    current_stock = None     # 个股新闻的当前股票
    current_code = None

    date_re = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})")
    cat_re = re.compile(r"(宏观/行业政策|关注股公告|个股新闻|宏观/政策)")
    stock_re = re.compile(r"\*\*([^*()]+)\((\d{6})\)\*\*:")
    ann_re = re.compile(r"^\s*•\s*\*\*([^*]+)\*\*\s+(.*?)\s*\[([^\]]*)\]")

    def flush():
        pass

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        m = date_re.match(raw.strip(" #"))
        # 标题栏（含日期）
        if raw.startswith("## ") and (mm := date_re.search(raw)):
            current_date = mm.group(1)
            current_stock = None
            category = None
            continue

        # 分类栏
        if "**" in line and cat_re.search(line.split("**")[1] if line.startswith("**") else line):
            # 匹配 "**宏观/行业政策**" 或 "📌 **个股新闻速览**"
            for key in ("宏观/行业政策", "宏观/政策", "关注股公告", "个股新闻"):
                if key in line:
                    category = {"宏观/行业政策": "宏观/政策",
                                "宏观/政策": "宏观/政策"}.get(key, key)
                    current_stock = None
                    break
            continue

        if category is None or current_date is None:
            continue

        # --- 个股新闻里的股票头 "**久立特材(002318)**:"
        if category == "个股新闻" and (sm := stock_re.search(raw)):
            current_stock = sm.group(1).strip()
            current_code = sm.group(2)
            continue

        # --- 关注股公告 "• **金力永磁** 金力永磁:H股公告... [其他]"
        if category == "关注股公告" and line.startswith("•"):
            am = ann_re.match(raw)
            if am:
                stock = am.group(1).strip()
                text = am.group(2).strip()
                etype = am.group(3).strip()
                entries.append({
                    "date": current_date, "category": "关注股公告",
                    "stock": stock, "code": "",
                    "text": f"{text} [{etype}]",
                })
            continue

        # --- 宏观/政策 与 个股新闻 的正文 bullet (• 或 -)
        if line.startswith("•") or line.startswith("-"):
            # 个股新闻正文形如 "- 久立特材002318.SZ)：..."
            # 前面的股票代码与正文混在一起，去掉开头的“股票名+代码”噪音
            text = line.lstrip("•- ").strip()
            if not text:
                continue
            if category == "个股新闻":
                # 去掉开头冗余噪音（股票名+代码前缀）：
                #   “久立特材002318.SZ)：xxx”  -> 去掉直到第一个全角冒号
                #   “601066，xxx” / “600036，xxx” -> 去掉前导 6 位代码+逗号
                text = re.sub(r"^\d{3}(\.SZ)?：", "", text)          # 00318.SZ)：
                text = re.sub(r"^[^：，]{0,20}?：", "", text)          # 到全角冒号
                text = re.sub(r"^\d{6}，", "", text)                  # 601066，
                text = text.strip()
            entries.append({
                "date": current_date,
                "category": category,
                "stock": current_stock if category == "个股新闻" else "",
                "code": current_code if category == "个股新闻" else "",
                "text": text,
            })

    return entries


# ---------------- 嵌入 ----------------
def embed_entries(entries, model_name=MODEL_NAME):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name, device="cpu")
    texts = [e["text"] for e in entries]
    print(f"编码 {len(texts)} 条文本 ...")
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=True,
                        batch_size=64)
    return np.asarray(vecs, dtype="float32")


# ---------------- 建索引 ----------------
def build_index(vecs):
    import faiss
    d = vecs.shape[1]
    index = faiss.IndexFlatIP(d)   # 内积。向量已归一化，内积 = 余弦相似度
    index.add(vecs)
    return index


def save(entries, vecs, index):
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    np.save(VECS_PATH, vecs)
    faiss.write_index(index, FAISS_PATH)
    print(f"✅ 已保存 {len(entries)} 条 -> {INDEX_DIR}")


def load():
    with open(META_PATH, encoding="utf-8") as f:
        entries = json.load(f)
    vecs = np.load(VECS_PATH)
    index = faiss.read_index(FAISS_PATH)
    return entries, vecs, index


# ---------------- 检索 ----------------
def search(query, topk=8, stock_filter=None, category_filter=None, model_name=MODEL_NAME):
    from sentence_transformers import SentenceTransformer
    entries, _, index = load()
    model = SentenceTransformer(model_name, device="cpu")
    q = np.asarray(model.encode([query], normalize_embeddings=True), dtype="float32")
    scores, ids = index.search(q, topk)

    rows = []
    for s, i in zip(scores[0], ids[0]):
        e = entries[i]
        if stock_filter and stock_filter not in e["stock"]:
            continue
        if category_filter and e["category"] != category_filter:
            continue
        rows.append((float(s), e))
    return rows


def main():
    ap = argparse.ArgumentParser(description="新闻知识库批量建索引 + 检索")
    ap.add_argument("--skip-embed", action="store_true", help="用已有向量只重建 faiss 索引")
    ap.add_argument("--search-only", action="store_true", help="索引已存在时只检索，不重建")
    ap.add_argument("--search", help="给定查询，建完（或读完）后直接检索")
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--stock", default=None, help="按股票名过滤")
    ap.add_argument("--category", default=None, help="按分类过滤: 宏观/政策|关注股公告|个股新闻")
    args = ap.parse_args()

    if args.search_only and os.path.exists(FAISS_PATH):
        if args.search:
            _do_search(args)
            return

    entries = parse_kb()
    print(f"解析出 {len(entries)} 条新闻条目")

    cats = {}
    for e in entries:
        cats[e["category"]] = cats.get(e["category"], 0) + 1
    print("分类统计:", cats)

    if args.skip_embed and os.path.exists(VECS_PATH):
        print("--skip-embed: 复用已有向量")
        vecs = np.load(VECS_PATH)
        if len(vecs) != len(entries):
            print("⚠️ 向量数(%d)与条目数(%d)不一致，忽略 --skip-embed 重新嵌入"
                  % (len(vecs), len(entries)))
            vecs = embed_entries(entries)
    else:
        vecs = embed_entries(entries)

    index = build_index(vecs)
    save(entries, vecs, index)

    if args.search:
        _do_search(args)


def _do_search(args):
    from sentence_transformers import SentenceTransformer
    entries, _, index = load()
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    q = np.asarray(model.encode([args.search], normalize_embeddings=True, show_progress_bar=False),
                   dtype="float32")
    scores, ids = index.search(q, args.topk)
    print()
    print("=" * 60)
    print(f"检索: 「{args.search}」  (top {args.topk})"
          + (f"  [股票={args.stock}]" if args.stock else "")
          + (f"  [分类={args.category}]" if args.category else ""))
    print("=" * 60)
    cnt = 0
    for s, i in zip(scores[0], ids[0]):
        e = entries[i]
        if args.stock and args.stock not in e["stock"]:
            continue
        if args.category and e["category"] != args.category:
            continue
        tag = f"[{e['date']}]"
        if e["stock"]:
            tag += f" {e['stock']}({e['code']})"
        tag += f" {e['category']}"
        print(f"  {s:.3f}  {tag}")
        print(f"        {e['text'][:90]}")
        cnt += 1
    if cnt == 0:
        print("(无结果，可放宽过滤条件)")


if __name__ == "__main__":
    main()
