"""Vector store wrapper around ChromaDB for semantic profile search.

Profile data is chunked into sections (skills summary, per-role descriptions,
education, etc.) and embedded into a local ChromaDB collection.  The
:meth:`semantic_search` method lets the tailoring engine find the most
relevant profile sections for a given job description.
"""

from __future__ import annotations as _annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog

from core.config import get_settings
from core.exceptions import ProfileError

__all__: list[str] = [
    "VectorStore",
]

logger = structlog.get_logger(__name__)

# Default ChromaDB collection name for profile embeddings.
_DEFAULT_COLLECTION = "profile_chunks"
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # Default sentence-transformers model


class VectorStore:
    """ChromaDB-backed vector store for profile section embeddings.

    Usage::

        store = VectorStore()
        await store.store_profile_embedding(
            profile_id=profile.id,
            text_chunks=[
                ("skills", "Python, Rust, Kubernetes, …"),
                ("experience", "Senior Engineer at Acme Corp …"),
            ],
        )
        results = await store.semantic_search("distributed systems engineer", n_results=5)
    """

    def __init__(self, persist_directory: str | Path | None = None, collection_name: str = _DEFAULT_COLLECTION) -> None:
        self._persist_directory: Path
        if persist_directory is not None:
            self._persist_directory = Path(persist_directory)
        else:
            self._persist_directory = get_settings().data_dir / "chroma"

        self._collection_name = collection_name
        self._client: Any = None  # chromadb.PersistentClient
        self._collection: Any = None  # chromadb.Collection
        self._started = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise the ChromaDB persistent client and collection.

        This must be called before any read/write operations.
        """
        try:
            import chromadb  # noqa: PLC0415
        except ImportError as exc:
            msg = (
                "The ``chromadb`` package is required for the vector store. "
                "Install it with: uv pip install chromadb"
            )
            raise ProfileError(msg) from exc

        self._persist_directory.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(self._persist_directory),
            settings=chromadb.Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )

        # Get or create the collection.
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._started = True
        logger.info(
            "ChromaDB vector store started",
            persist_directory=str(self._persist_directory),
            collection=self._collection_name,
            embedding_function=str(self._collection._embedding_function),
        )

    async def stop(self) -> None:
        """Tear down the ChromaDB client."""
        self._started = False
        if self._client is not None:
            self._client.clear_system_cache()
            self._client = None
        logger.info("ChromaDB vector store stopped")

    # ── Operations ────────────────────────────────────────────────────────────

    async def store_profile_embedding(
        self,
        profile_id: uuid.UUID | str,
        text_chunks: Sequence[tuple[str, str]],
    ) -> int:
        """Embed and store profile text chunks into the vector index.

        Each chunk is a ``(section_name, text)`` pair, for example:

        - ``("skills", "Python, Rust, async programming, …")``
        - ``("experience:acme-corp", "Led the redesign of …")``

        Existing chunks for *profile_id* are replaced.

        Args:
            profile_id: The profile UUID these chunks belong to.
            text_chunks: Sequence of ``(section_name, text)`` pairs.

        Returns:
            The number of chunks stored.

        Raises:
            ProfileError: If the store has not been started.
        """
        self._require_started()

        pid = str(profile_id)
        ids: list[str] = []
        metadatas: list[dict[str, str]] = []
        documents: list[str] = []

        # Delete existing entries for this profile (idempotent re-storage).
        try:
            existing = self._collection.get(where={"profile_id": pid})
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
        except Exception:
            logger.debug("No existing embeddings to clear for profile", profile_id=pid)

        for section_name, text in text_chunks:
            chunk_id = f"{pid}:{section_name}:{uuid.uuid4().hex[:8]}"
            ids.append(chunk_id)
            metadatas.append({"profile_id": pid, "section": section_name})
            documents.append(text)

        if not ids:
            return 0

        self._collection.add(
            ids=ids,
            metadatas=metadatas,
            documents=documents,
        )
        logger.debug("Stored profile embeddings", profile_id=pid, chunks=len(ids))
        return len(ids)

    async def semantic_search(
        self,
        query: str,
        *,
        n_results: int = 10,
        profile_id: uuid.UUID | str | None = None,
    ) -> list[dict[str, Any]]:
        """Search the vector store for profile chunks relevant to *query*.

        Args:
            query: A natural-language query (e.g. a job description snippet).
            n_results: Maximum number of results to return.
            profile_id: If provided, restrict results to a single profile.

        Returns:
            A list of result dicts, each containing:
            - ``id``: The chunk ID.
            - ``profile_id``: The owning profile.
            - ``section``: The chunk section name (e.g. ``"skills"``).
            - ``text``: The stored text.
            - ``score``: Cosine distance (0 = identical).
        """
        self._require_started()

        where: dict[str, Any] = {}
        if profile_id is not None:
            where["profile_id"] = str(profile_id)

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where or None,
            )
        except Exception as exc:
            msg = f"Semantic search query failed: {exc}"
            raise ProfileError(msg) from exc

        if not results or not results["ids"]:
            return []

        output: list[dict[str, Any]] = []
        for i, chunk_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            output.append(
                {
                    "id": chunk_id,
                    "profile_id": metadata.get("profile_id", ""),
                    "section": metadata.get("section", "unknown"),
                    "text": results["documents"][0][i] if results["documents"] else "",
                    "score": results["distances"][0][i] if results["distances"] else 0.0,
                }
            )

        # Sort by ascending distance (closest first).
        output.sort(key=lambda r: r["score"])
        return output

    async def delete_profile_embeddings(self, profile_id: uuid.UUID | str) -> int:
        """Remove all embeddings for a given profile.

        Returns:
            The number of embeddings removed.
        """
        self._require_started()
        pid = str(profile_id)

        try:
            existing = self._collection.get(where={"profile_id": pid})
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
                logger.debug("Deleted profile embeddings", profile_id=pid, count=len(existing["ids"]))
                return len(existing["ids"])
        except Exception as exc:
            logger.warning("Error deleting profile embeddings", profile_id=pid, error=str(exc))

        return 0

    async def collection_size(self) -> int:
        """Return the total number of chunks stored in the collection."""
        self._require_started()
        return self._collection.count()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _require_started(self) -> None:
        """Guard: raise :class:`ProfileError` if the store has not been started."""
        if not self._started or self._collection is None:
            msg = (
                "VectorStore has not been started. "
                "Call ``await store.start()`` before performing operations."
            )
            raise ProfileError(msg)
