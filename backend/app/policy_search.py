"""
policy_search.py
Minimal RAG layer over the markdown policy documents in /backend/policies.
Uses TF-IDF + cosine similarity (scikit-learn) so the demo has no dependency
on an external embeddings API. In production you'd swap this for a vector DB
(e.g. pgvector, Pinecone, Weaviate) and real embeddings, but the interface
(`search_policies(query, k)`) would stay the same.
"""

import os
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_DIR = os.path.join(BASE_DIR, "policies")


def _chunk_markdown(text, source):
    """Split a markdown file into chunks along header boundaries (## or deeper)
    so retrieval returns focused, citeable sections rather than whole documents
    or, worse, a single bloated section covering several unrelated reasons."""
    parts = re.split(r"\n(?=#{2,6} )", text)
    chunks = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        chunks.append({"source": source, "text": part})
    return chunks


class PolicyIndex:
    def __init__(self):
        self.chunks = []
        self._load()
        self.vectorizer = TfidfVectorizer(stop_words="english")
        corpus = [c["text"] for c in self.chunks]
        self.matrix = self.vectorizer.fit_transform(corpus) if corpus else None

    def _load(self):
        for fname in sorted(os.listdir(POLICY_DIR)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(POLICY_DIR, fname)
            with open(path) as f:
                text = f.read()
            self.chunks.extend(_chunk_markdown(text, fname.replace(".md", "")))

    def search(self, query, k=3):
        if self.matrix is None or len(self.chunks) == 0:
            return []
        query_vec = self.vectorizer.transform([query])
        sims = cosine_similarity(query_vec, self.matrix).flatten()
        top_idx = sims.argsort()[::-1][:k]
        results = []
        for i in top_idx:
            if sims[i] <= 0:
                continue
            results.append({
                "source": self.chunks[i]["source"],
                "text": self.chunks[i]["text"],
                "relevance": round(float(sims[i]), 3),
            })
        return results


_index = None


def get_index():
    global _index
    if _index is None:
        _index = PolicyIndex()
    return _index


def search_policies(query, k=3):
    return get_index().search(query, k)
