#!/usr/bin/env python3
"""
经验库（RAG）实现：从 memory/*.md 中抽取经验句子，
优先使用 BAAI/bge-m3 + FAISS；若torch/faiss不可用，则退化为
TF-IDF + 余弦相似度（基于 scikit-learn），保持相同的接口。
"""

import os, json, re, pickle
from typing import List, Tuple
import numpy as np

# Try to import heavy deps (torch-dependent)
try:
    import faiss   # pip install faiss-cpu
    from sentence_transformers import SentenceTransformer
    _HAS_TORCH_RAG = True
except Exception:
    _HAS_TORCH_RAG = False
    faiss = None
    SentenceTransformer = None

# Fallback: sklearn TF-IDF
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SKLEARN = True
except Exception:
    _HAS_SKLEARN = False
    TfidfVectorizer = None
    cosine_similarity = None

if _HAS_TORCH_RAG:
    # 使用你本地已经在运行的 BGE‑M3 模型（保持与 memory 系统一致）
    EMBED_MODEL = SentenceTransformer("BAAI/bge-m3")
else:
    EMBED_MODEL = None

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
EXPERIENCE_DIR = os.path.join(WORKSPACE, "memory")
INDEX_PATH = os.path.join(WORKSPACE, "data", "experience.index")
METADATA_PATH = os.path.join(WORKSPACE, "data", "experience_meta.pkl")
# For TFIDF fallback
VECTORIZER_PATH = os.path.join(WORKSPACE, "data", "experience_vectorizer.pkl")
TFIDF_MATRIX_PATH = os.path.join(WORKSPACE, "data", "experience_tfidf_matrix.pkl")

def _extract_experience_sentences() -> List[str]:
    """从 memory/*.md 中抽出看起来像经验或教训的句子。
 这里采用简单的正则：寻找包含“经验”、“教训”、“注意”、“警示”、“避免”等关键词的句子。
    """
    experiences = []
    for root, _, files in os.walk(EXPERIENCE_DIR):
        for f in files:
            if f.endswith(".md"):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        text = fp.read()
                except Exception:
                    continue
                # 按中文句号、英文句号、换行分割
                sentences = re.split(r'[。\.!\n]+', text)
                for s in sentences:
                    s = s.strip()
                    if len(s) < 10:
                        continue
                    if any(k in s for k in ["经验", "教训", "注意", "警示", "避免", "错误", "失误", "提醒"]):
                        experiences.append(s)
    return experiences

def _build_torch_index() -> Tuple[object, List[str]]:
    """构建 torch+faiss 索引。"""
    if not _HAS_TORCH_RAG:
        raise RuntimeError("torch RAG dependencies not available")
    sentences = _extract_experience_sentences()
    if not sentences:
        dim = EMBED_MODEL.get_sentence_embedding_dimension()
        index = faiss.IndexFlatIP(dim)
        return index, []
    embeds = EMBED_MODEL.encode(sentences, normalize_embeddings=True)
    dim = embeds.shape[1]
    index = faiss.IndexFlatIP(dim)   # 内积等价于余弦相似度（已归一化）
    index.add(np.array(embeds, dtype="float32"))
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    faiss.write_index(index, INDEX_PATH)
    with open(METADATA_PATH, "wb") as f:
        pickle.dump(sentences, f)
    return index, sentences

def _load_torch_index() -> Tuple[object, List[str]]:
    if not (_HAS_TORCH_RAG and os.path.exists(INDEX_PATH) and os.path.exists(METADATA_PATH)):
        raise FileNotFoundError("Torch index not found")
    index = faiss.read_index(INDEX_PATH)
    with open(METADATA_PATH, "rb") as f:
        meta = pickle.load(f)
    return index, meta

def _build_sklearn_index() -> Tuple[TfidfVectorizer, np.ndarray, List[str]]:
    if not _HAS_SKLEARN:
        raise RuntimeError("sklearn not available")
    sentences = _extract_experience_sentences()
    if not sentences:
        # Return empty vectorizer and matrix
        vectorizer = TfidfVectorizer()
        matrix = np.zeros((0, 0))
        return vectorizer, matrix, []
    vectorizer = TfidfVectorizer()
    matrix = vectorizer.fit_transform(sentences)
    # Save
    os.makedirs(os.path.dirname(VECTORIZER_PATH), exist_ok=True)
    with open(VECTORIZER_PATH, "wb") as f:
        pickle.dump(vectorizer, f)
    with open(TFIDF_MATRIX_PATH, "wb") as f:
        pickle.dump(matrix, f)
    with open(METADATA_PATH, "wb") as f:
        pickle.dump(sentences, f)
    return vectorizer, matrix, sentences

def _load_sklearn_index() -> Tuple[TfidfVectorizer, np.ndarray, List[str]]:
    if not (_HAS_SKLEARN and os.path.exists(VECTORIZER_PATH) and os.path.exists(TFIDF_MATRIX_PATH) and os.path.exists(METADATA_PATH)):
        raise FileNotFoundError("Sklearn index not found")
    with open(VECTORIZER_PATH, "rb") as f:
        vectorizer = pickle.load(f)
    with open(TFIDF_MATRIX_PATH, "rb") as f:
        matrix = pickle.load(f)
    with open(METADATA_PATH, "rb") as f:
        sentences = pickle.load(f)
    return vectorizer, matrix, sentences

def build_or_load_index():
    """
 返回 (index_or_vectorizer, sentences_or_matrix, sentences_list, mode)
    mode: 'torch' or 'sklearn' or 'none'
    """
    # Prefer torch if available
    if _HAS_TORCH_RAG:
        try:
            return _load_torch_index() + (None, 'torch')
        except Exception:
            # fallback to rebuild
            try:
                return _build_torch_index() + (None, 'torch')
            except Exception:
                pass  # fall through to sklearn
    # Try sklearn
    if _HAS_SKLEARN:
        try:
            vec, mat, sents = _load_sklearn_index()
            return (vec, mat, sents, 'sklearn')
        except Exception:
            try:
                vec, mat, sents = _build_sklearn_index()
                return (vec, mat, sents, 'sklearn')
            except Exception:
                pass
    # No usable backend
    return (None, None, [], 'none')

def retrieve_top_k(query: str, k: int = 3) -> List[str]:
    """返回与 query 最相关的 k 条经验句子。
 根据可用的后端选择相应的检索方式。
    """
    idx, data, sentences, mode = build_or_load_index()
    if mode == 'none' or not sentences:
        return []
    if mode == 'torch':
        # idx is faiss Index, data unused
        q_emb = EMBED_MODEL.encode([query], normalize_embeddings=True)
        D, I = idx.search(np.array(q_emb, dtype="float32"), k)
        return [sentences[i] for i in I[0] if i < len(sentences)]
    elif mode == 'sklearn':
        # idx is TfidfVectorizer, data is tfidf matrix
        vectorizer = idx
        matrix = data
        q_vec = vectorizer.transform([query])
        sims = cosine_similarity(q_vec, matrix)[0]  # shape (n_sentences,)
        # Get top k indices
        top_idx = np.argsort(sims)[::-1][:k]
        return [sentences[i] for i in top_idx if i < len(sentences)]
    else:
        return []

# 若想直接测试
if __name__ == "__main__":
    exp = retrieve_top_k("今日市场情绪与操作建议", k=3)
    for i, e in enumerate(exp, 1):
        print(f"{i}. {e}")