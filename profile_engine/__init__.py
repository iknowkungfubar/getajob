"""Profile Engine — User profile storage, vector search, and experience parsing.

Provides the :class:`ProfileStore` (SQLAlchemy CRUD with transparent PII
encryption), the :class:`VectorStore` (ChromaDB semantic search over profile
chunks), and the :class:`ExperienceParser` (skill extraction and resume
parsing).
"""

from __future__ import annotations as _annotations

__all__: list[str] = [
    "ProfileStore",
    "VectorStore",
    "ExperienceParser",
]

from profile_engine.profile_store import ProfileStore
from profile_engine.vector_store import VectorStore
from profile_engine.experience_parser import ExperienceParser
