"""SQLAlchemy ORM models for the GetAJob platform.

Covers job listings, user profiles, work experience, applications, and
application-state events.  All timestamped models include ``created_at``
and ``updated_at`` columns with automatic update triggers.
"""

from __future__ import annotations as _annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship

# ── SQLite type compilers ────────────────────────────────────────────────────
# The ORM models use PostgreSQL-specific types (UUID, JSONB).  These @compiles
# directives tell SQLAlchemy how to render them against SQLite, enabling local
# development without a running PostgreSQL instance.


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(element: Any, compiler: Any, **kw: object) -> str:  # type: ignore[misc]  # noqa: ARG001
    """Render PostgreSQL UUID as VARCHAR(36) for SQLite."""
    return "VARCHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element: Any, compiler: Any, **kw: object) -> str:  # type: ignore[misc]  # noqa: ARG001
    """Render PostgreSQL JSONB as generic JSON for SQLite."""
    return "JSON"

from core.database import Base
from core.state_machine import ApplicationState

__all__: list[str] = [
    "Application",
    "ApplicationEvent",
    "JobListing",
    "UserProfile",
    "WorkExperience",
]


def _utcnow() -> datetime.datetime:
    """Return the current UTC timestamp (naive, for DB storage)."""
    return datetime.datetime.now(datetime.UTC)


def _generate_uuid() -> uuid.UUID:
    """Return a new UUID v4."""
    return uuid.uuid4()


# ── Mixins ───────────────────────────────────────────────────────────────────────


class _TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns to a model."""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=_utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


# ── Job Listing ──────────────────────────────────────────────────────────────────


class JobListing(_TimestampMixin, Base):
    """A job listing discovered from any source (LinkedIn, Greenhouse, etc.)."""

    __tablename__ = "job_listings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=_generate_uuid,
    )
    source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Source platform, e.g. linkedin, indeed, greenhouse",
    )
    source_id: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="Source-internal identifier (URL hash, posting ID)",
    )
    company: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    location: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
    description_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Full raw description and parsed sections (structured JSON)",
    )
    url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    posted_date: Mapped[datetime.date | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    required_skills: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="List of skills extracted from the description",
    )
    salary_range: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="e.g. {\"min\": 150000, \"max\": 200000, \"currency\": \"USD\"}",
    )
    form_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="ATS type detected: workday, greenhouse, lever, linkedin, etc.",
    )
    is_active: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
        comment="Soft-delete / expiry flag",
    )

    # ── Relationships ────────────────────────────────────────────────────────
    applications: Mapped[list[Application]] = relationship(
        "Application",
        back_populates="job_listing",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# ── User Profile ─────────────────────────────────────────────────────────────────


class UserProfile(_TimestampMixin, Base):
    """The user's immutable master profile.

    PII fields (``email``, ``phone``) are stored encrypted at rest by the
    :class:`~profile_engine.profile_store.ProfileStore` layer — the column
    values here are raw ciphertext strings.
    """

    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=_generate_uuid,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="Monotonic version counter for the immutable-ledger pattern",
    )
    name: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Encrypted at rest via AES-256-GCM",
    )
    phone: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Encrypted at rest via AES-256-GCM",
    )
    location: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
    linkedin_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    portfolio_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    work_authorization: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="e.g. US Citizen, GC, H1B, etc.",
    )
    skills: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="List of skill objects: [{name, category, proficiency}]",
    )
    answers: Mapped[dict[str, str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Pre-written answers to common application questions",
    )
    is_active: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────────
    work_experiences: Mapped[list[WorkExperience]] = relationship(
        "WorkExperience",
        back_populates="profile",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WorkExperience.start_date.desc()",
    )
    applications: Mapped[list[Application]] = relationship(
        "Application",
        back_populates="profile",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# ── Work Experience ──────────────────────────────────────────────────────────────


class WorkExperience(_TimestampMixin, Base):
    """A single position on the user's work history."""

    __tablename__ = "work_experiences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=_generate_uuid,
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
    )
    start_date: Mapped[datetime.date | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    end_date: Mapped[datetime.date | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    skills_used: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Skills exercised in this role",
    )
    is_current: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────────
    profile: Mapped[UserProfile] = relationship(
        "UserProfile",
        back_populates="work_experiences",
    )


# ── Application ──────────────────────────────────────────────────────────────────


class Application(_TimestampMixin, Base):
    """Tracks one job application through its entire lifecycle."""

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=_generate_uuid,
    )
    job_listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    state: Mapped[ApplicationState] = mapped_column(
        Enum(ApplicationState, name="application_state", create_constraint=True),
        nullable=False,
        default=ApplicationState.DISCOVERED,
        index=True,
    )
    resume_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Tailored resume text (PDF rendering happens at submission time)",
    )
    cover_letter: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    recruiter_email: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Encrypted at rest",
    )
    recruiter_name: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
    applied_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
        comment="When the application was actually submitted",
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Human reviewer notes",
    )

    # ── Relationships ────────────────────────────────────────────────────────
    job_listing: Mapped[JobListing] = relationship(
        "JobListing",
        back_populates="applications",
    )
    profile: Mapped[UserProfile] = relationship(
        "UserProfile",
        back_populates="applications",
    )
    events: Mapped[list[ApplicationEvent]] = relationship(
        "ApplicationEvent",
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApplicationEvent.timestamp",
    )


# ── Application Event (Audit Log) ────────────────────────────────────────────────


class ApplicationEvent(Base):
    """Immutable audit-log entry recording every application state transition."""

    __tablename__ = "application_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=_generate_uuid,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_state: Mapped[ApplicationState | None] = mapped_column(
        Enum(ApplicationState, name="application_state_event_from"),
        nullable=True,
    )
    to_state: Mapped[ApplicationState] = mapped_column(
        Enum(ApplicationState, name="application_state_event_to"),
        nullable=False,
    )
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=_utcnow,
        nullable=False,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Arbitrary metadata (reason, reviewer, error details, etc.)",
    )

    # ── Relationships ────────────────────────────────────────────────────────
    application: Mapped[Application] = relationship(
        "Application",
        back_populates="events",
    )
