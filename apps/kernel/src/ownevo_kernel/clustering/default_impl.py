"""Production wiring for the clustering pipeline (B3.2).

All heavy deps (`sentence-transformers`, `umap-learn`, `hdbscan`,
`anthropic`) are imported lazily inside the constructors so unit tests
that stub the Protocols never trigger the imports. They land via the
`clustering` extra (`pip install ownevo-kernel[clustering]`).

The default model + dimensions are pinned to match the schema:
  - sentence-transformers/all-MiniLM-L6-v2  →  384-dim
  - UMAP n_components=8 (Leland McInnes' "8-15 is fine for HDBSCAN" rule)
  - HDBSCAN min_cluster_size=3, min_samples=2
  - Anthropic claude-haiku-4-5 for labeling (cheap; cluster labels are
    short factual descriptions, not reasoning).

If a production caller wants to swap any of these, they can build their
own `Embedder`/`Reducer`/`Clusterer`/`Labeler` and pass them directly to
`cluster_failures` — these defaults are convenience, not contract.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from .types import EMBEDDING_DIM, RawClusterAssignment

if TYPE_CHECKING:  # pragma: no cover — types only
    from anthropic import Anthropic


_log = logging.getLogger(__name__)


_DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_LABEL_MODEL = "claude-haiku-4-5-20251001"


class SentenceTransformerEmbedder:
    """Lazy wrapper around sentence-transformers all-MiniLM-L6-v2.

    The model is loaded on first `.embed()` call so process startup
    stays fast for the cases that don't need it.
    """

    def __init__(self, model_name: str = _DEFAULT_EMBED_MODEL) -> None:
        self.model_name = model_name
        self._model: Any | None = None

    def _ensure(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover — exercise path
                raise ImportError(
                    "SentenceTransformerEmbedder requires the `clustering` "
                    "extra: `pip install ownevo-kernel[clustering]`",
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        model = self._ensure()
        out = model.encode(
            texts,
            batch_size=32,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        if out.shape[1] != EMBEDDING_DIM:
            raise ValueError(
                f"{self.model_name} returned dim={out.shape[1]} "
                f"but the schema expects {EMBEDDING_DIM}.",
            )
        return out.astype(np.float32, copy=False)


class UMAPReducer:
    """Lazy UMAP wrapper. Defaults tuned for HDBSCAN downstream."""

    def __init__(
        self,
        *,
        n_components: int = 8,
        n_neighbors: int = 15,
        min_dist: float = 0.0,
        random_state: int = 42,
    ) -> None:
        self.n_components = n_components
        self.n_neighbors = n_neighbors
        self.min_dist = min_dist
        self.random_state = random_state

    def reduce(self, embeddings: np.ndarray) -> np.ndarray:
        try:
            import umap
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "UMAPReducer requires the `clustering` extra: "
                "`pip install ownevo-kernel[clustering]`",
            ) from exc
        # n_neighbors must be < n_samples; clamp so tiny inputs don't crash.
        n_neighbors = min(self.n_neighbors, max(2, embeddings.shape[0] - 1))
        n_components = min(self.n_components, max(2, embeddings.shape[0] - 1))
        reducer = umap.UMAP(
            n_components=n_components,
            n_neighbors=n_neighbors,
            min_dist=self.min_dist,
            metric="cosine",
            random_state=self.random_state,
        )
        return reducer.fit_transform(embeddings)


class HDBSCANClusterer:
    """Lazy HDBSCAN wrapper. Surfaces per-cluster persistence."""

    def __init__(
        self,
        *,
        min_cluster_size: int = 3,
        min_samples: int = 2,
        cluster_selection_epsilon: float = 0.0,
    ) -> None:
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples
        self.cluster_selection_epsilon = cluster_selection_epsilon

    def cluster(self, reduced: np.ndarray) -> RawClusterAssignment:
        try:
            import hdbscan
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "HDBSCANClusterer requires the `clustering` extra: "
                "`pip install ownevo-kernel[clustering]`",
            ) from exc
        # Clamp min_cluster_size so a 5-point input doesn't auto-fail.
        mcs = min(self.min_cluster_size, max(2, reduced.shape[0] // 2))
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=mcs,
            min_samples=self.min_samples,
            cluster_selection_epsilon=self.cluster_selection_epsilon,
            metric="euclidean",
        )
        labels = clusterer.fit_predict(reduced).astype(np.int64, copy=False)
        # `cluster_persistence_` is parallel to `np.unique(labels[labels >= 0])`.
        persistence: dict[int, float] = {}
        unique = sorted(set(int(x) for x in labels.tolist()) - {-1})
        if unique and hasattr(clusterer, "cluster_persistence_"):
            for lbl, p in zip(unique, clusterer.cluster_persistence_, strict=True):
                persistence[int(lbl)] = float(p)
        return RawClusterAssignment(labels=labels, persistence=persistence)


class AnthropicLabeler:
    """One-line cluster labeler over Anthropic Messages API.

    Returns a short noun-phrase, e.g., "winter footwear in Pacific NW Q4".
    The schema column is `text NOT NULL`; we never return an empty
    string (the pipeline falls back to `cluster-<n>` if we ever do).
    """

    def __init__(
        self,
        client: Anthropic | None = None,
        *,
        model: str = _DEFAULT_LABEL_MODEL,
        max_tokens: int = 64,
    ) -> None:
        self._client = client
        self.model = model
        self.max_tokens = max_tokens

    def _ensure(self) -> Anthropic:
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "AnthropicLabeler requires the `agent` extra: "
                    "`pip install ownevo-kernel[agent]`",
                ) from exc
            self._client = Anthropic()
        return self._client

    def label(self, sample_texts: list[str], cluster_index: int) -> str:
        client = self._ensure()
        prompt_samples = "\n".join(f"- {t}" for t in sample_texts)
        msg = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=(
                "You name failure clusters from forecasting models. Given a "
                "few example failures, respond with a short noun phrase "
                "(under 8 words) that names the shared failure mode. "
                "No quotes, no trailing period."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Cluster {cluster_index} contains these failures:\n"
                        f"{prompt_samples}\n\n"
                        "Respond with ONLY the cluster name."
                    ),
                }
            ],
        )
        # `msg.content` is a list of content blocks; we want the first text block.
        for block in msg.content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                return text.strip().splitlines()[0]
        _log.warning(
            "AnthropicLabeler: no text content in response (%s)",
            json.dumps(msg.model_dump()),
        )
        return f"cluster-{cluster_index}"
