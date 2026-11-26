"""Embeddings for profile retrieval.

Default: a deterministic local embedder (hashed words + character trigrams with
IDF weighting). It needs no model download and no API key, and gives fuzzy
matching across word forms and spelling variants. It is a lexical approximation
of semantic similarity, not a neural embedding. Set EMBEDDING_MODEL to use an
OpenAI-compatible /embeddings endpoint instead.
"""

from __future__ import annotations

import json
import math
import os
import urllib.request
import zlib

import numpy as np

from ..llm.http import ssl_context
from ..text import normalize, tokens


class HashingEmbedder:
    name = "hashing-v1"

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.idf = np.ones(dim, dtype=np.float32)

    def _features(self, text: str) -> list[int]:
        words = tokens(text)
        grams = []
        for word in normalize(text).split():
            padded = f"#{word}#"
            grams += [padded[i:i + 3] for i in range(len(padded) - 2)]
        return [zlib.crc32(f"w:{w}".encode()) % self.dim for w in words] + \
               [zlib.crc32(f"g:{g}".encode()) % self.dim for g in grams]

    def fit(self, texts: list[str]) -> None:
        df = np.zeros(self.dim, dtype=np.float32)
        for text in texts:
            for bucket in set(self._features(text)):
                df[bucket] += 1
        self.idf = np.log((len(texts) + 1) / (df + 1)).astype(np.float32) + 1.0

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for bucket in self._features(text):
                out[row, bucket] += 1.0
            out[row] = np.log1p(out[row]) * self.idf
            norm = np.linalg.norm(out[row])
            if norm:
                out[row] /= norm
        return out


class ApiEmbedder:
    """OpenAI-compatible embeddings endpoint (EMBEDDING_MODEL, EMBEDDING_BASE_URL, EMBEDDING_API_KEY)."""

    def __init__(self, model: str, base_url: str, api_key: str):
        self.name = f"api:{model}"
        self.model, self.base_url, self.api_key = model, base_url.rstrip("/"), api_key

    def fit(self, texts: list[str]) -> None:
        pass

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), 64):
            body = json.dumps({"model": self.model, "input": texts[start:start + 64]}).encode()
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            request = urllib.request.Request(f"{self.base_url}/embeddings", data=body, headers=headers)
            with urllib.request.urlopen(request, timeout=60, context=ssl_context()) as response:
                data = json.load(response)["data"]
            vectors += [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        out = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


def make_embedder():
    model = os.environ.get("EMBEDDING_MODEL", "").strip()
    if not model:
        return HashingEmbedder()
    return ApiEmbedder(model, os.environ.get("EMBEDDING_BASE_URL", "").strip() or os.environ.get("LLM_BASE_URL", "").strip()
                       or "https://api.openai.com/v1",
                       os.environ.get("EMBEDDING_API_KEY", "").strip() or os.environ.get("LLM_API_KEY", "").strip())


def bm25_scores(query_tokens: list[str], documents: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    count = len(documents)
    average = sum(len(d) for d in documents) / max(count, 1)
    df: dict[str, int] = {}
    for document in documents:
        for token in set(document):
            df[token] = df.get(token, 0) + 1
    scores = []
    for document in documents:
        tf: dict[str, int] = {}
        for token in document:
            tf[token] = tf.get(token, 0) + 1
        score = 0.0
        for token in set(query_tokens):
            if token not in tf:
                continue
            idf = math.log(1 + (count - df[token] + 0.5) / (df[token] + 0.5))
            score += idf * tf[token] * (k1 + 1) / (tf[token] + k1 * (1 - b + b * len(document) / (average or 1)))
        scores.append(score)
    return scores
