"""Zero-cost retrieval: TF-IDF + cosine similarity over the small FAQ/policy
corpus, not embeddings. Deliberate choice, not a placeholder — an embedding
API call costs money on every single query (including the ones a confident
FAQ match could answer for free), and this corpus is small enough that
keyword-overlap retrieval works well while keeping the entire tier-1 RAG
path at $0 forever, preserving the whole budget for the Council tier (see
app/domains/support/nodes.py). Swappable behind the same Protocol if a
larger, more paraphrase-tolerant corpus later justifies embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.rag.corpus import CORPUS, KnowledgeEntry


@dataclass(frozen=True, slots=True)
class RetrievalMatch:
    entry: KnowledgeEntry
    score: float


class Retriever(Protocol):
    def search(self, query: str, top_k: int = 3) -> list[RetrievalMatch]: ...


class TfidfRetriever:
    def __init__(self, corpus: tuple[KnowledgeEntry, ...] = CORPUS) -> None:
        self._corpus = corpus
        # Question + answer + tags all feed the index — a query phrased
        # like the answer (e.g. "reversal timeline") should still match.
        documents = [
            f"{e.question} {e.answer} {' '.join(e.tags)}" for e in corpus
        ]
        self._vectorizer = TfidfVectorizer(stop_words="english")
        self._matrix = self._vectorizer.fit_transform(documents)

    def search(self, query: str, top_k: int = 3) -> list[RetrievalMatch]:
        if not query.strip():
            return []
        query_vector = self._vectorizer.transform([query])
        scores = cosine_similarity(query_vector, self._matrix)[0]
        ranked = sorted(
            zip(self._corpus, scores), key=lambda pair: pair[1], reverse=True
        )
        return [RetrievalMatch(entry=entry, score=float(score)) for entry, score in ranked[:top_k]]
