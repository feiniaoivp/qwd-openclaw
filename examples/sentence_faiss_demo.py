#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sentence_faiss_demo.py
======================
torch + sentence-transformers + faiss 联动示例
------------------------------------------------
1. torch：张量基本运算（CPU）
2. sentence-transformers：把句子转成向量（BGE 中文小模型）
3. faiss：建索引 + 相似度检索（余弦相似度 / FlatIP）

运行（Intel Mac, 独立 venv）：
    /Users/duguke/.openclaw/workspace/.venv-ml/bin/python \
        /Users/duguke/.openclaw/workspace/examples/sentence_faiss_demo.py

可选参数：
    --query "你的查询句子"   （默认用第一句测试）
    --topk 3              （返回前几相似）
    --model BAAI/bge-small-zh-v1.5
"""

import argparse
import numpy as np

# ---------- 1. torch 基本用法（CPU 张量） ----------
import torch

# ⚠️ Intel Mac + torch 2.2.2 多线程 encode 会段错误，强制单线程
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
torch.set_num_threads(1)

def demo_torch():
    print("=" * 60)
    print("[1] torch 张量基础  (device = %s)" % torch.device("cpu"))
    print("=" * 60)

    a = torch.randn(3, 4)
    b = torch.ones(3, 4)
    s = a @ b.T                     # (3,4) @ (4,3) -> (3,3)
    print("a shape      :", tuple(a.shape))
    print("a @ b.T shape:", tuple(s.shape))
    print("sum a        : %.4f" % a.sum().item())
    print("max over dim0:", a.max(dim=0).values.tolist())

    # 向量 -> 归一化（和后面 embedding 归一化一个道理）
    v = torch.tensor([3.0, 4.0])
    vn = torch.nn.functional.normalize(v, dim=0)
    print("vector [3,4] norm ->", vn.tolist(), "(L2 = %.3f)" % vn.norm().item())


# ---------- 2 & 3. 嵌入 + faiss 检索 ----------
from sentence_transformers import SentenceTransformer
import faiss


def build_documents():
    """示例语料库（股票/财经方向，贴合使用场景）"""
    return [
        "中芯国际是国内领先的半导体晶圆代工厂",
        "长电科技主营集成电路封测业务",
        "通富微电是国内主要芯片封测企业之一",
        "国瓷材料做电子陶瓷材料，股价波动较大",
        "招商银行是股份制银行，业绩稳健",
        "天齐锂业主营锂矿资源和锂盐产品",
        "福耀玻璃是汽车玻璃龙头，海外收入占比高",
        "中信证券是头部券商，经纪和投行业务强",
        "今天股市大涨，半导体板块集体涨停",
        "美联储加息预期升温，成长股承压",
    ]


def demo_embed_and_search(model_name, query, topk):
    print()
    print("=" * 60)
    print(f"[2] 句子嵌入  model = {model_name}")
    print("=" * 60)

    model = SentenceTransformer(model_name, device="cpu")
    docs = build_documents()

    # 对全部文档编码 + 归一化（余弦相似度要求归一化）
    emb = model.encode(docs, normalize_embeddings=True, show_progress_bar=False)
    emb = np.asarray(emb, dtype="float32")
    print(f"语料 {len(docs)} 句 -> 向量矩阵 {emb.shape} (每句 {emb.shape[1]} 维)")

    print()
    print("=" * 60)
    print("[3] faiss 索引 + 检索  (FlatIP = 内积 ≈ 余弦相似度)")
    print("=" * 60)

    # 建索引并加入全部向量
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    print(f"index.ntotal = {index.ntotal}")

    # 对查询句编码
    q = np.asarray(model.encode([query], normalize_embeddings=True), dtype="float32")

    # 检索 top-k
    scores, ids = index.search(q, topk)
    print(f"\n查询: 「{query}」\n")
    for rank, (doc_id, score) in enumerate(zip(ids[0], scores[0]), 1):
        print(f"  #{rank}  相似度 {score:.3f}  {docs[doc_id]}")

    return index


def main():
    ap = argparse.ArgumentParser(description="torch + sentence-transformers + faiss 演示")
    ap.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    ap.add_argument("--query", default=None, help="默认用第一条语料做查询")
    ap.add_argument("--topk", type=int, default=3)
    args = ap.parse_args()

    demo_torch()
    index = demo_embed_and_search(args.model, args.query or build_documents()[0], args.topk)

    print()
    print("=" * 60)
    print("✅ 完成：torch 张量运算 + 文本嵌入 + faiss 检索全部跑通")


if __name__ == "__main__":
    main()
