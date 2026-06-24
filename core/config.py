"""Configuration loading for the GetAJob platform.

Settings are resolved in this order (last wins):
  1. Default values on the model fields.
  2. YAML overlay from ``config/settings.yaml`` (if present).
  3. Environment variables prefixed with ``GETAJOB_``.
  4. ``.env`` file (if present, loaded via python-dotenv).

Usage::

    from core.config import get_settings

    settings = get_settings()
    print(settings.database.host)
"""

from __future__ import annotations as _annotations

from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.exceptions import ConfigurationError

__all__: list[str] = [
    "GetAJobSettings",
    "get_settings",
    "load_config",
]

logger = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Nested settings groups ───────────────────────────────────────────────────────


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection settings."""

    model_config = SettingsConfigDict(extra="ignore")

    host: str = Field(default="localhost", description="Database host")
    port: int = Field(default=5432, ge=1, le=65535, description="Database port")
    database: str = Field(default="getajob", description="Database name")
    user: str = Field(default="getajob", description="Database user")
    password: str = Field(default="", description="Database password")
    min_connections: int = Field(default=2, ge=1, description="Connection pool min size")
    max_connections: int = Field(default=10, ge=1, description="Connection pool max size")

    @property
    def dsn(self) -> str:
        """Build an async PostgreSQL DSN from the component fields."""
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    @property
    def dsn_sync(self) -> str:
        """Build a synchronous PostgreSQL DSN (for Alembic migrations)."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


class LLMSettings(BaseSettings):
    """LLM provider settings."""

    model_config = SettingsConfigDict(extra="ignore")

    provider: str = Field(default="anthropic", description="LLM provider (anthropic, openai, local)")
    api_key: str = Field(default="", description="API key for the LLM provider")
    model: str = Field(default="claude-sonnet-4-6", description="Model identifier")
    max_tokens: int = Field(default=4096, ge=64, le=65536, description="Max response tokens")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    timeout_seconds: int = Field(default=120, ge=5, description="Request timeout")


class RedisSettings(BaseSettings):
    """Redis connection settings (async event bus and caching)."""

    model_config = SettingsConfigDict(extra="ignore")

    host: str = Field(default="localhost", description="Redis host")
    port: int = Field(default=6379, ge=1, le=65535, description="Redis port")
    db: int = Field(default=0, ge=0, le=15, description="Redis database index")
    password: str | None = Field(default=None, description="Redis password")
    socket_timeout_seconds: int = Field(default=5, ge=1, description="Socket timeout")

    @property
    def dsn(self) -> str:
        """Build a Redis DSN."""
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class SecuritySettings(BaseSettings):
    """Encryption and PII security settings."""

    model_config = SettingsConfigDict(extra="ignore")

    encryption_key: str = Field(default="", description="AES-256-GCM key (hex-encoded, 64 hex chars = 32 bytes)")
    encryption_salt: str = Field(default="", description="PBKDF2 salt (hex-encoded, 32 hex chars = 16 bytes)")
    tokenizer_salt: str = Field(default="", description="PII tokenizer salt (hex-encoded, 32 hex chars = 16 bytes)")
    approval_password: str = Field(default="", description="HITL approval queue web UI password (separate from DB password)")


class BrowserSettings(BaseSettings):
    """Browser automation settings."""

    model_config = SettingsConfigDict(extra="ignore")

    headless: bool = Field(default=True, description="Run browser in headless mode")
    viewport_width: int = Field(default=1920, ge=640, description="Browser viewport width")
    viewport_height: int = Field(default=1080, ge=480, description="Browser viewport height")
    locale: str = Field(default="en-US", description="Browser locale")
    proxy: str | None = Field(default=None, description="Proxy URL (http://user:pass@host:port)")
    user_data_dir: str | None = Field(default=None, description="Persistent browser profile directory")
    slow_mo_ms: int = Field(default=50, ge=0, le=2000, description="Slow-motion delay (ms) for stealth")
    navigation_timeout_seconds: int = Field(default=30, ge=5, description="Page navigation timeout")


class OutreachSettings(BaseSettings):
    """Recruiter contact discovery and outreach settings."""

    model_config = SettingsConfigDict(extra="ignore")

    max_lookups_per_minute: int = Field(
        default=15, ge=1, le=120, description="Global rate limit for contact lookups (per minute)"
    )
    max_contacts_per_company: int = Field(
        default=5, ge=1, le=20, description="Max recruiter contacts to search per company"
    )
    email_patterns: list[str] = Field(
        default_factory=lambda: [
            "firstname@company.com",
            "first.last@company.com",
            "firstname.lastname@company.com",
        ],
        description="Common email patterns to probe",
    )
    respect_robots_txt: bool = Field(
        default=True, description="Skip scraping if robots.txt disallows"
    )
    http_timeout_seconds: int = Field(
        default=15, ge=5, le=60, description="HTTP request timeout for lookups"
    )
    user_agent: str = Field(
        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        description="User-Agent header for web requests",
    )


class JobDiscoverySettings(BaseSettings):
    """Job search and discovery configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    max_applications_per_day: int = Field(default=50, ge=1, le=200, description="Daily application cap")
    rate_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "linkedin": 10,
            "indeed": 15,
            "greenhouse": 30,
            "workday": 20,
            "lever": 30,
        },
        description="Max queries per minute per source",
    )
    cache_ttl_hours: int = Field(default=24, ge=1, description="How long to cache seen listings")
    search_filters: dict[str, Any] = Field(
        default_factory=lambda: {
            "locations": ["remote", "san francisco", "new york"],
            "seniority": ["senior", "staff", "principal", "lead"],
            "remote_only": False,
        },
        description="Default search filters",
    )


# ── Root settings ────────────────────────────────────────────────────────────────


class GetAJobSettings(BaseSettings):
    """Root configuration for the entire GetAJob platform.

    Loaded from environment variables (prefix ``GETAJOB_``), an optional
    ``.env`` file, and an optional YAML overlay at ``config/settings.yaml``.
    """

    model_config = SettingsConfigDict(
        env_prefix="GETAJOB_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Nested groups ────────────────────────────────────────────────────────
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    outreach: OutreachSettings = Field(default_factory=OutreachSettings)
    job_discovery: JobDiscoverySettings = Field(default_factory=JobDiscoverySettings)

    # ── Top-level knobs ──────────────────────────────────────────────────────
    debug: bool = Field(default=False, description="Enable debug-level logging")
    log_format: str = Field(default="json", description="Log format: json or console")
    environment: str = Field(default="development", description="Runtime environment")
    data_dir: Path = Field(
        default=_PROJECT_ROOT / "data",
        description="Root data directory",
    )

    # ── Validators ───────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _warn_if_insecure_defaults(self) -> GetAJobSettings:
        """Emit a warning when critical settings use default values."""
        if not self.security.encryption_key:
            logger.warning(
                "encryption_key is empty — PII at rest will NOT be encrypted. "
                "Set GETAJOB_SECURITY__ENCRYPTION_KEY in .env for production."
            )
        if not self.llm.api_key and self.llm.provider == "anthropic":
            logger.warning(
                "LLM API key is empty — the platform will fail at application time "
                "unless MockLLMClient is used. Set GETAJOB_LLM__API_KEY in .env."
            )
        return self

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v.lower() not in allowed:
            msg = f"environment must be one of {allowed}, got {v!r}"
            raise ConfigurationError(msg)
        return v.lower()

    @field_validator("data_dir")
    @classmethod
    def _ensure_data_dir(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v


# ── Config overlay loader ────────────────────────────────────────────────────────


def load_config(yaml_path: Path | None = None) -> dict[str, Any]:
    """Load the YAML overlay file and return its contents as a plain dict.

    The overlay lets operators tweak search vectors, rate limits, and
    ATS profiles without touching environment variables.
    """
    if yaml_path is None:
        yaml_path = _PROJECT_ROOT / "config" / "settings.yaml"

    if not yaml_path.exists():
        logger.info("No YAML config overlay found at %s — using defaults", yaml_path)
        return {}

    with yaml_path.open("r") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        logger.warning("YAML config overlay is not a dict — ignoring")
        return {}

    logger.info("Loaded YAML config overlay from %s (%d top-level keys)", yaml_path, len(data))
    return data


# ── Module-level singleton ───────────────────────────────────────────────────────

_settings: GetAJobSettings | None = None


def get_settings() -> GetAJobSettings:
    """Return the module-level settings singleton.

    Calling this function from anywhere in the codebase guarantees a single
    configuration object is reused for the process lifetime.
    """
    global _settings
    if _settings is None:
        # pydantic-settings already reads .env via python-dotenv when
        # model_config has an `env_file` directive — we set it here so the
        # .env path is resolved relative to the project root.
        env_path = _PROJECT_ROOT / ".env"
        _settings = GetAJobSettings(_env_file=str(env_path) if env_path.exists() else None)  # type: ignore[call-arg]
    return _settings
