"""Profile storage and retrieval with transparent PII encryption.

Implements the **immutable ledger** pattern: changes create a new version
rather than mutating rows in place, preserving an append-only history.
PII fields (``email``, ``phone``) are encrypted at rest via AES-256-GCM
and decrypted transparently on read.
"""

from __future__ import annotations as _annotations

import datetime
import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from core.config import get_settings
from core.exceptions import ProfileError, SecurityError
from core.models import UserProfile, WorkExperience
from core.schemas import ProfileCreate, ProfileRead, ProfileUpdate, SkillSchema, WorkExperienceSchema
from core.security import decrypt_value, encrypt_value, derive_key

__all__: list[str] = [
    "ProfileStore",
]

logger = structlog.get_logger(__name__)


class ProfileStore:
    """CRUD operations for the user profile with transparent encryption.

    Usage::

        store = ProfileStore(engine)
        profile = await store.create_profile(data)
        loaded = await store.get_profile(profile.id)
        exported = await store.export_profile(profile.id, path="profile_export.json")
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._encryption_key: bytes | None = None

    # ── Key management ───────────────────────────────────────────────────────

    def _get_key(self) -> bytes:
        """Lazily derive the AES-256 key from configuration.

        Returns a 32-byte key.  Raises :class:`ProfileError` if the
        encryption key is not configured.
        """
        if self._encryption_key is not None:
            return self._encryption_key

        settings = get_settings()
        raw_key = settings.security.encryption_key
        raw_salt = settings.security.encryption_salt

        if not raw_key:
            msg = (
                "Encryption key is not configured. "
                "Set GETAJOB_SECURITY__ENCRYPTION_KEY in .env."
            )
            raise ProfileError(msg)

        if not raw_salt:
            # Default to a zero salt when none is configured (still encrypts).
            raw_salt = "00" * 16

        key, _ = derive_key(raw_key, bytes.fromhex(raw_salt))
        self._encryption_key = key
        return key

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt a PII string."""
        try:
            return encrypt_value(plaintext, self._get_key())
        except SecurityError as exc:
            raise ProfileError("Failed to encrypt PII field") from exc

    def _decrypt(self, ciphertext: str) -> str:
        """Decrypt a PII string."""
        try:
            return decrypt_value(ciphertext, self._get_key())
        except SecurityError as exc:
            raise ProfileError("Failed to decrypt PII field") from exc

    # ── CRUD ─────────────────────────────────────────────────────────────────

    async def create_profile(self, data: ProfileCreate, session: AsyncSession | None = None) -> ProfileRead:
        """Create a new profile with encrypted PII.

        Args:
            data: The profile creation payload.
            session: An optional existing database session.  If omitted, a new
                session is created and committed automatically.

        Returns:
            The newly created profile (PII decrypted for the response).
        """
        from core.database import get_session  # noqa: PLC0415

        async def _do(session: AsyncSession) -> ProfileRead:
            skills_json = _skills_to_json(data.skills) if data.skills else None

            profile = UserProfile(
                name=data.name,
                email=self._encrypt(data.email),
                phone=self._encrypt(data.phone),
                location=data.location,
                linkedin_url=data.linkedin_url,
                portfolio_url=data.portfolio_url,
                work_authorization=data.work_authorization,
                skills=skills_json,
                answers=data.answers,
            )
            session.add(profile)
            await session.flush()  # Get the generated ID.

            # Persist work experiences.
            if data.work_experiences:
                for we in data.work_experiences:
                    exp = WorkExperience(
                        profile_id=profile.id,
                        company=we.company,
                        title=we.title,
                        start_date=we.start_date,
                        end_date=we.end_date,
                        description=we.description,
                        skills_used=we.skills_used,
                        is_current=we.is_current,
                    )
                    session.add(exp)
                await session.flush()

            return await self._profile_to_read(profile, session)

        if session is not None:
            return await _do(session)
        async with get_session(self._engine) as s:
            return await _do(s)

    async def get_profile(self, profile_id: uuid.UUID, session: AsyncSession | None = None) -> ProfileRead | None:
        """Retrieve a profile by ID.

        Returns:
            The profile with decrypted PII, or ``None`` if not found.
        """
        from core.database import get_session  # noqa: PLC0415

        async def _do(session: AsyncSession) -> ProfileRead | None:
            result = await session.execute(
                select(UserProfile).where(UserProfile.id == profile_id, UserProfile.is_active.is_(True))
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                return None
            return await self._profile_to_read(profile, session)

        if session is not None:
            return await _do(session)
        async with get_session(self._engine) as s:
            return await _do(s)

    async def update_profile(
        self,
        profile_id: uuid.UUID,
        data: ProfileUpdate,
        session: AsyncSession | None = None,
    ) -> ProfileRead:
        """Update an existing profile.

        Implements the immutable-ledger pattern: bumps the version counter
        and writes a new row, then soft-deletes the old one.

        Args:
            profile_id: UUID of the profile to update.
            data: Patch payload (only provided fields are changed).
            session: An optional existing session.

        Returns:
            The updated profile (new version).
        """
        from core.database import get_session  # noqa: PLC0415

        async def _do(session: AsyncSession) -> ProfileRead:
            # Load current active profile.
            result = await session.execute(
                select(UserProfile).where(UserProfile.id == profile_id, UserProfile.is_active.is_(True))
            )
            current = result.scalar_one_or_none()
            if current is None:
                msg = f"Active profile {profile_id} not found"
                raise ProfileError(msg)

            # Build the new version with merged data.
            skills_json = _skills_to_json(data.skills) if data.skills else current.skills

            new_profile = UserProfile(
                id=current.id,  # Same logical ID, new version.
                version=current.version + 1,
                name=data.name or current.name,
                email=self._encrypt(data.email) if data.email else current.email,
                phone=self._encrypt(data.phone) if data.phone else current.phone,
                location=data.location if data.location is not None else current.location,
                linkedin_url=data.linkedin_url if data.linkedin_url is not None else current.linkedin_url,
                portfolio_url=data.portfolio_url if data.portfolio_url is not None else current.portfolio_url,
                work_authorization=(
                    data.work_authorization if data.work_authorization is not None else current.work_authorization
                ),
                skills=skills_json,
                answers=data.answers if data.answers is not None else current.answers,
                is_active=True,
            )
            session.add(new_profile)
            await session.flush()

            # Soft-delete the old version.
            await session.execute(
                update(UserProfile)
                .where(UserProfile.id == profile_id, UserProfile.version == current.version)
                .values(is_active=False)
            )

            # Handle work experience updates: replace all entries.
            if data.work_experiences is not None:
                # Remove old experiences.
                old_exps = await session.execute(
                    select(WorkExperience).where(WorkExperience.profile_id == profile_id)
                )
                for old in old_exps.scalars():
                    await session.delete(old)
                await session.flush()

                for we in data.work_experiences:
                    exp = WorkExperience(
                        profile_id=profile_id,
                        company=we.company,
                        title=we.title,
                        start_date=we.start_date,
                        end_date=we.end_date,
                        description=we.description,
                        skills_used=we.skills_used,
                        is_current=we.is_current,
                    )
                    session.add(exp)
                await session.flush()

            return await self._profile_to_read(new_profile, session)

        if session is not None:
            return await _do(session)
        async with get_session(self._engine) as s:
            return await _do(s)

    async def list_profiles(
        self,
        session: AsyncSession | None = None,
        *,
        skip: int = 0,
        limit: int = 20,
    ) -> Sequence[ProfileRead]:
        """List all active profiles with pagination.

        Returns:
            A sequence of profiles (PII decrypted).
        """
        from core.database import get_session  # noqa: PLC0415

        async def _do(session: AsyncSession) -> Sequence[ProfileRead]:
            result = await session.execute(
                select(UserProfile)
                .where(UserProfile.is_active.is_(True))
                .order_by(UserProfile.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            profiles = result.scalars().all()
            return [await self._profile_to_read(p, session) for p in profiles]

        if session is not None:
            return await _do(session)
        async with get_session(self._engine) as s:
            return await _do(s)

    # ── Export / Import ───────────────────────────────────────────────────────

    async def export_profile(self, profile_id: uuid.UUID, file_path: str | Path | None = None) -> dict[str, Any]:
        """Export a profile as a JSON-serialisable dict (PII decrypted).

        Args:
            profile_id: The profile UUID to export.
            file_path: Optional filesystem path.  If provided, the JSON is
                written to disk at this location.

        Returns:
            The profile as a plain Python dict ready for ``json.dump``.
        """
        from core.database import get_session  # noqa: PLC0415

        async with get_session(self._engine) as session:
            profile = await self.get_profile(profile_id, session=session)
            if profile is None:
                msg = f"Profile {profile_id} not found for export"
                raise ProfileError(msg)

            export: dict[str, Any] = profile.model_dump(mode="json")
            # Include work experience as nested dicts.
            result = await session.execute(
                select(WorkExperience)
                .where(WorkExperience.profile_id == profile_id)
                .order_by(WorkExperience.start_date.desc())
            )
            export["work_experiences"] = [
                {
                    "company": we.company,
                    "title": we.title,
                    "start_date": we.start_date.isoformat() if we.start_date else None,
                    "end_date": we.end_date.isoformat() if we.end_date else None,
                    "description": we.description,
                    "skills_used": we.skills_used,
                    "is_current": we.is_current,
                }
                for we in result.scalars().all()
            ]

            if file_path:
                path = Path(file_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(export, indent=2, default=str))
                logger.info("Profile exported to file", profile_id=str(profile_id), path=str(path))

            return export

    async def import_profile(self, file_path: str | Path) -> ProfileRead:
        """Import a profile from a JSON export file.

        Args:
            file_path: Path to the JSON export.

        Returns:
            The newly created profile.
        """
        path = Path(file_path)
        if not path.exists():
            msg = f"Import file not found: {path}"
            raise ProfileError(msg)

        raw = json.loads(path.read_text())
        data = ProfileCreate(**raw)
        profile = await self.create_profile(data)
        logger.info("Profile imported from file", profile_id=str(profile.id), path=str(file_path))
        return profile

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _profile_to_read(self, profile: UserProfile, session: AsyncSession) -> ProfileRead:
        """Convert an ORM UserProfile + WorkExperience rows to a ProfileRead schema."""
        skills_schema: list[SkillSchema] | None = None
        if profile.skills:
            skills_schema = [SkillSchema(**s) if isinstance(s, dict) else s for s in profile.skills]

        # Load work experiences.
        result = await session.execute(
            select(WorkExperience)
            .where(WorkExperience.profile_id == profile.id)
            .order_by(WorkExperience.start_date.desc())
        )
        we_schema = [
            WorkExperienceSchema(
                company=we.company,
                title=we.title,
                start_date=we.start_date.date() if we.start_date else None,
                end_date=we.end_date.date() if we.end_date else None,
                description=we.description,
                skills_used=we.skills_used,
                is_current=we.is_current,
            )
            for we in result.scalars().all()
        ]

        return ProfileRead(
            id=profile.id,
            version=profile.version,
            name=profile.name,
            email=self._decrypt(profile.email),
            phone=self._decrypt(profile.phone),
            location=profile.location,
            linkedin_url=profile.linkedin_url,
            portfolio_url=profile.portfolio_url,
            work_authorization=profile.work_authorization,
            skills=skills_schema,
            work_experiences=we_schema,
            answers=profile.answers,
            is_active=profile.is_active,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


# ── Module-level helpers ─────────────────────────────────────────────────────────


def _skills_to_json(skills: list[SkillSchema]) -> list[dict[str, Any]]:
    """Convert SkillSchema objects to JSON-compatible dicts."""
    return [s.model_dump(mode="json") for s in skills]
