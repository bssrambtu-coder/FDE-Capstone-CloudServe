"""Retrieval over the supplied documentation corpus (A4).

Two implementations behind one interface:

  LexicalRetriever  - BM25 over the corpus, pure stdlib, no model download.
  ChromaRetriever   - the stack's prescribed store; falls back to lexical if
                      chromadb or the embedding model is unavailable.

Both apply a relevance floor and return nothing when nothing clears it. The
Build Specification is explicit that always returning something hides failure,
and 28.6% of development tickets have no relevant document at all.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

from .models import Passage

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9_]+")
_STOP = frozenset(
    """a an the and or but if then than that this these those is are was were be been being
    do does did doing have has had having i we you he she it they them our your my me us
    to of in on at for with from by as about into over after before under between out up
    down off no not so such own same too very can will just should now also there here
    what which who whom when where why how all any both each few more most other some
    only own s t don ll re ve y""".split()
)


def tokenise(text: str) -> list[str]:
    """Plain tokenisation. Used by the classifier, whose committed vocabulary
    was fitted on these exact tokens - do not add stemming here without
    refitting models/."""
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2]


# Light suffix stripping, applied to retrieval only. Longest suffix first.
#
# Adopted after the fairness audit: it lifts overall retrieval hit rate from
# 80.1% to 87.4% at an unchanged relevance floor, because tickets from
# non-fluent speakers use different inflections of the same words as the
# documentation ("builds that work last week are now fail"). It narrows nothing
# on its own - see docs/fairness_audit.md - but it is a free quality gain.
_SUFFIXES = (
    "izations", "ization", "ations", "tional", "ement", "ments", "ingly",
    "ness", "ment", "tion", "sion", "ance", "ence", "able", "ible", "ings",
    "ing", "ied", "ies", "ers", "er", "ed", "es", "ly", "s",
)


def stem(word: str) -> str:
    for suffix in _SUFFIXES:
        # Keep at least four characters, so "es" does not reduce "res" to "r".
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def retrieval_tokens(text: str) -> list[str]:
    return [stem(w) for w in tokenise(text)]


def load_corpus(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("documents", "docs", "data", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    return [d for d in data if isinstance(d, dict) and d.get("doc_id")]


class Retriever(Protocol):
    def search(self, query: str, *, top_k: int = 5) -> list[Passage]: ...


class LexicalRetriever:
    """BM25. Deterministic, dependency-free, and strong on a 29-document corpus.

    Scores are normalised against the best achievable score for the query so
    that the relevance floor means the same thing across queries of different
    lengths — an absolute BM25 score is not comparable between queries.
    """

    name = "lexical-bm25"

    def __init__(self, corpus: list[dict], *, floor: float = 0.42, k1: float = 1.5, b: float = 0.75):
        self.floor = floor
        self.k1, self.b = k1, b
        self.docs = corpus
        self._tokens: list[list[str]] = []
        self._tf: list[Counter] = []
        for doc in corpus:
            blob = " ".join(
                str(doc.get(f, "")) for f in ("title", "category", "applies_to", "content")
            )
            toks = retrieval_tokens(blob)
            self._tokens.append(toks)
            self._tf.append(Counter(toks))
        self._lengths = [len(t) for t in self._tokens]
        self._avg_len = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        n = len(corpus)
        df = Counter()
        for toks in self._tokens:
            df.update(set(toks))
        # +1 inside the log keeps every idf positive, so a term present in
        # every document contributes nothing rather than scoring negative.
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        # A term absent from the corpus is maximally rare. Counting it in the
        # normalising mass is what makes coverage matter: a six-word query that
        # matches one word scores near a sixth, not near one.
        self._unseen_idf = max(self._idf.values(), default=1.0)

    def _score(self, q_tokens: list[str], i: int) -> float:
        tf, length = self._tf[i], self._lengths[i]
        if not length:
            return 0.0
        total = 0.0
        for term in q_tokens:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = self._idf.get(term, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * length / (self._avg_len or 1))
            total += idf * f * (self.k1 + 1) / denom
        return total

    def search(self, query: str, *, top_k: int = 5) -> list[Passage]:
        q = retrieval_tokens(query)
        if not q:
            return []
        # Normalise by the query's total idf mass, counting terms the corpus
        # has never seen, so the relevance floor means the same thing for a
        # three-word query and a thirty-word one and a query that matches only
        # one of its words cannot score as if it matched all of them.
        mass = sum(self._idf.get(t, self._unseen_idf) for t in set(q))
        if mass <= 0:
            return []
        scored = []
        for i, doc in enumerate(self.docs):
            s = self._score(q, i) / mass
            if s >= self.floor:
                scored.append(
                    Passage(
                        doc_id=str(doc["doc_id"]),
                        title=str(doc.get("title", "")),
                        text=str(doc.get("content", "")),
                        score=round(s, 4),
                    )
                )
        # doc_id as secondary key keeps ordering stable when scores tie (A5).
        scored.sort(key=lambda p: (-p.score, p.doc_id))
        return scored[:top_k]


class ChromaRetriever:
    """The prescribed stack: Chroma + all-MiniLM-L6-v2.

    Constructed lazily and degrades to the lexical retriever if the dependency
    or the model is missing, so a clean checkout without the optional extras
    still clears A4.
    """

    name = "chroma-minilm"

    def __init__(self, corpus: list[dict], *, floor: float = 0.42, path: str = "storage/chroma"):
        self.floor = floor
        self.fallback = LexicalRetriever(corpus, floor=floor)
        self._collection = None
        try:
            import chromadb
            from chromadb.utils import embedding_functions
        except ImportError:
            log.info("chromadb not installed, retrieval using %s", self.fallback.name)
            return
        try:
            client = chromadb.PersistentClient(path=path)
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
            coll = client.get_or_create_collection("cloudserve_docs", embedding_function=ef)
            if coll.count() != len(corpus):
                coll.upsert(
                    ids=[str(d["doc_id"]) for d in corpus],
                    documents=[f"{d.get('title','')}\n{d.get('content','')}" for d in corpus],
                    metadatas=[{"title": str(d.get("title", ""))} for d in corpus],
                )
            self._collection = coll
        except Exception as exc:  # a broken vector store must not stop the run
            log.warning("chroma unavailable (%s), retrieval using %s", exc, self.fallback.name)

    def search(self, query: str, *, top_k: int = 5) -> list[Passage]:
        if self._collection is None:
            return self.fallback.search(query, top_k=top_k)
        try:
            res = self._collection.query(query_texts=[query], n_results=top_k)
        except Exception as exc:
            log.warning("chroma query failed (%s), degrading to lexical", exc)
            return self.fallback.search(query, top_k=top_k)
        out = []
        for doc_id, text, meta, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            score = 1.0 / (1.0 + float(dist))  # cosine distance to a similarity
            if score >= self.floor:
                out.append(
                    Passage(doc_id=str(doc_id), title=str((meta or {}).get("title", "")),
                            text=str(text), score=round(score, 4))
                )
        out.sort(key=lambda p: (-p.score, p.doc_id))
        return out


def build_retriever(corpus_path: str, *, floor: float = 0.42, backend: str = "lexical") -> Retriever:
    corpus = load_corpus(corpus_path)
    if backend == "chroma":
        return ChromaRetriever(corpus, floor=floor)
    return LexicalRetriever(corpus, floor=floor)
