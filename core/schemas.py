"""Pydantic v2 schemas for all GetAJob API interfaces.

Every data model used at the system boundary (HTTP API, event bus, agent I/O)
is defined here.  Schemas follow a strict separation between *Create* (input),
*Read* (output), and *Update* (patch) variants.
"""

from __future__ import annotations as _annotations

import datetime
import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.state_machine import ApplicationState

__all__: list[str] = [
    # Generic
    "PaginatedResponse",
    "ErrorResponse",
    # Job listing
    "JobListingCreate",
    "JobListingRead",
    "JobListingUpdate",
    # Profile
    "SkillSchema",
    "WorkExperienceSchema",
    "ProfileCreate",
    "ProfileRead",
    "ProfileUpdate",
    # Work experience (standalone)
    "WorkExperienceCreate",
    "WorkExperienceRead",
    # Application
    "ApplicationCreate",
    "ApplicationRead",
    "ApplicationStateUpdate",
    "ApplicationEventRead",
    # Tailoring
    "TailoringRequest",
    "TailoringResponse",
    # Browser
    "BrowserSubmissionRequest",
    "BrowserSubmissionResponse",
    # Outreach
    "RecruiterInfoSchema",
    "OutreachMessageSchema",
    # Config
    "SearchVectorConfig",
    "ATSProfileConfig",
]

_T = TypeVar("_T")


# ── Generic containers ───────────────────────────────────────────────────────────


class PaginatedResponse(BaseModel, Generic[_T]):
    """Generic wrapper for paginated API responses."""

    items: Sequence[_T]
    total: int
    page: int
    page_size: int
    total_pages: int


class ErrorResponse(BaseModel):
    """Standard error body returned by the API."""

    detail: str = Field(..., description="Human-readable error description")
    error_code: str | None = Field(default=None, description="Machine-readable error code")
    details: dict[str, Any] | None = Field(default=None, description="Additional error context")


# ── Job Listing Schemas ──────────────────────────────────────────────────────────


class JobListingCreate(BaseModel):
    """Input schema for creating a new job listing."""

    model_config = ConfigDict(extra="ignore")

    source: str = Field(..., max_length=64, description="Source platform name")
    source_id: str | None = Field(default=None, max_length=256, description="Source-internal identifier")
    company: str = Field(..., max_length=256)
    title: str = Field(..., max_length=512)
    location: str | None = Field(default=None, max_length=256)
    description_json: dict[str, Any] | None = Field(default=None)
    url: str | None = Field(default=None, max_length=2048)
    posted_date: datetime.datetime | None = Field(default=None)
    required_skills: list[str] | None = Field(default=None)
    salary_range: dict[str, Any] | None = Field(default=None)
    form_type: str | None = Field(default=None, max_length=64)


class JobListingRead(BaseModel):
    """Output schema for a job listing returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    source_id: str | None
    company: str
    title: str
    location: str | None
    description_json: dict[str, Any] | None
    url: str | None
    posted_date: datetime.datetime | None
    required_skills: list[str] | None
    salary_range: dict[str, Any] | None
    form_type: str | None
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class JobListingUpdate(BaseModel):
    """Patch schema for an existing job listing."""

    model_config = ConfigDict(extra="ignore")

    location: str | None = None
    description_json: dict[str, Any] | None = None
    url: str | None = None
    required_skills: list[str] | None = None
    salary_range: dict[str, Any] | None = None
    form_type: str | None = None
    is_active: bool | None = None


# ── Profile Schemas ──────────────────────────────────────────────────────────────


class SkillSchema(BaseModel):
    """A single skill entry on a profile."""

    name: str = Field(..., max_length=128)
    category: str | None = Field(default=None, max_length=64, description="e.g. language, framework, tool")
    proficiency: str | None = Field(default=None, max_length=32, description="e.g. expert, proficient, familiar")


class WorkExperienceSchema(BaseModel):
    """A single position in the user's work history."""

    company: str = Field(..., max_length=256)
    title: str = Field(..., max_length=256)
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    description: str | None = None
    skills_used: list[str] | None = None
    is_current: bool = False


class ProfileCreate(BaseModel):
    """Input schema for creating a user profile."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., max_length=256)
    email: str = Field(..., max_length=512)
    phone: str = Field(..., max_length=64)
    location: str | None = Field(default=None, max_length=256)
    linkedin_url: str | None = Field(default=None, max_length=1024)
    portfolio_url: str | None = Field(default=None, max_length=1024)
    work_authorization: str | None = Field(default=None, max_length=64)
    skills: list[SkillSchema] | None = None
    work_experiences: list[WorkExperienceSchema] | None = None
    answers: dict[str, str] | None = None


class ProfileUpdate(BaseModel):
    """Patch schema for updating an existing profile."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    work_authorization: str | None = None
    skills: list[SkillSchema] | None = None
    work_experiences: list[WorkExperienceSchema] | None = None
    answers: dict[str, str] | None = None

    @field_validator("email", "phone")
    @classmethod
    def _not_empty(cls, v: str | None) -> str | None:
        """Reject empty-string PII values that would silently erase data."""
        if v is not None and not v.strip():
            raise ValueError("PII field cannot be empty string")
        return v


class ProfileRead(BaseModel):
    """Output schema for a profile returned from the API.

    PII fields (``email``, ``phone``) are decrypted before serialization.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    name: str
    email: str
    phone: str
    location: str | None
    linkedin_url: str | None
    portfolio_url: str | None
    work_authorization: str | None
    skills: list[SkillSchema] | None
    work_experiences: list[WorkExperienceSchema] | None
    answers: dict[str, str] | None
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WorkExperienceCreate(BaseModel):
    """Input schema for adding a new work experience entry."""

    company: str = Field(..., max_length=256)
    title: str = Field(..., max_length=256)
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    description: str | None = None
    skills_used: list[str] | None = None
    is_current: bool = False


class WorkExperienceRead(BaseModel):
    """Output schema for a work experience entry."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    company: str
    title: str
    start_date: datetime.date | None
    end_date: datetime.date | None
    description: str | None
    skills_used: list[str] | None
    is_current: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ── Application Schemas ──────────────────────────────────────────────────────────


class ApplicationCreate(BaseModel):
    """Input schema for creating a new application record."""

    model_config = ConfigDict(extra="ignore")

    job_listing_id: uuid.UUID
    profile_id: uuid.UUID


class ApplicationStateUpdate(BaseModel):
    """Input schema for transitioning an application to a new state."""

    target_state: ApplicationState
    reason: str | None = Field(default=None, max_length=1024, description="Why the transition was made")
    metadata_json: dict[str, Any] | None = None


class ApplicationEventRead(BaseModel):
    """Output schema for an application-state transition event."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    from_state: ApplicationState | None
    to_state: ApplicationState
    timestamp: datetime.datetime
    metadata_json: dict[str, Any] | None


class ApplicationRead(BaseModel):
    """Output schema for an application returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_listing_id: uuid.UUID
    profile_id: uuid.UUID
    state: ApplicationState
    resume_text: str | None
    cover_letter: str | None
    recruiter_email: str | None
    recruiter_name: str | None
    applied_at: datetime.datetime | None
    notes: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    events: list[ApplicationEventRead] | None = None
    job_listing: JobListingRead | None = None


# ── Tailoring Schemas ────────────────────────────────────────────────────────────


class TailoringRequest(BaseModel):
    """Input for the tailoring engine — match profile to job."""

    model_config = ConfigDict(extra="ignore")

    job_listing_id: uuid.UUID
    profile_id: uuid.UUID
    generate_cover_letter: bool = True
    style_instructions: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional instructions for tone, length, etc.",
    )


class TailoringResponse(BaseModel):
    """Output from the tailoring engine."""

    application_id: uuid.UUID
    resume_text: str
    cover_letter: str | None = None
    matched_skills: list[str] = Field(default_factory=list)
    match_score: float | None = Field(default=None, ge=0.0, le=1.0, description="Semantic match score")
    warnings: list[str] = Field(default_factory=list, description="Anti-hallucination guardrail warnings")


# ── Browser Submission Schemas ───────────────────────────────────────────────────


class BrowserSubmissionRequest(BaseModel):
    """Input for the browser execution engine to submit an application."""

    model_config = ConfigDict(extra="ignore")

    application_id: uuid.UUID
    submit: bool = Field(
        default=False,
        description="If False, navigate to the final review page and pause for HITL",
    )


class BrowserSubmissionResponse(BaseModel):
    """Result of a browser submission attempt."""

    application_id: uuid.UUID
    success: bool
    screenshot_path: str | None = None
    error: str | None = None
    submitted_at: datetime.datetime | None = None


# ── Outreach Schemas ─────────────────────────────────────────────────────────────


class RecruiterInfoSchema(BaseModel):
    """Contact information for a recruiter or hiring manager discovered
    through OSINT techniques."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(default=None, description="Full name of the contact")
    title: str | None = Field(default=None, description="Job title (e.g. Talent Acquisition Manager)")
    email: str | None = Field(default=None, description="Email address")
    linkedin_url: str | None = Field(default=None, description="LinkedIn profile URL")
    confidence_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence in this contact's accuracy"
    )
    source: str = Field(default="unknown", description="Discovery method: job_listing, linkedin, website, email_pattern")
    company: str | None = Field(default=None, description="Company name")
    notes: str | None = Field(default=None, description="Additional context about this contact")


class OutreachMessageSchema(BaseModel):
    """A recruiter outreach message ready for human review."""

    model_config = ConfigDict(from_attributes=True)

    application_id: uuid.UUID
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    recruiter_title: str | None = None
    recruiter_linkedin: str | None = None
    subject: str = ""
    body: str = ""
    platform: str = Field(default="email", description="email, linkedin, etc.")
    tone: str = Field(default="professional", description="professional, warm, concise")
    generated_at: datetime.datetime | None = None


# ── Config Schemas ───────────────────────────────────────────────────────────────


class SearchVectorConfig(BaseModel):
    """A single search-vector entry from ``config/settings.yaml``."""

    model_config = ConfigDict(extra="ignore")

    roles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    seniority: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    max_applications_per_day: int | None = None


class ATSProfileConfig(BaseModel):
    """Per-ATS navigation profile configuration."""

    model_config = ConfigDict(extra="ignore")

    login_url: str | None = None
    application_url: str | None = None
    selectors: dict[str, str] = Field(default_factory=dict)
    delays: dict[str, float] = Field(default_factory=dict)
