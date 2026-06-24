"""GetAJob — Core framework.

Shared infrastructure: configuration, database, security, state machine,
event bus, LLM client, and exception hierarchy.
"""

from __future__ import annotations as _annotations

__all__ = [
    "ALLOWED_TRANSITIONS",
    # State machine
    "ApplicationState",
    # Database
    "Base",
    "BrowserError",
    "ClaudeAPIClient",
    "ConfigurationError",
    # Event bus
    "EventBus",
    "EventPriority",
    # Exceptions
    "GetAJobError",
    # Config
    "GetAJobSettings",
    "InMemoryEventBus",
    "IngestionError",
    # LLM
    "LLMClient",
    "MockLLMClient",
    "OutreachError",
    "ProfileError",
    "SecurityError",
    "StateMachineError",
    "TailoringError",
    "create_engine",
    "decrypt_value",
    "derive_key",
    "detokenize_pii",
    # Security
    "encrypt_value",
    "get_session",
    "load_config",
    "tokenize_pii",
    "transition_state",
]

from core.config import GetAJobSettings, load_config
from core.database import Base, create_engine, get_session
from core.event_bus import EventBus, EventPriority, InMemoryEventBus
from core.exceptions import (
    BrowserError,
    ConfigurationError,
    GetAJobError,
    IngestionError,
    OutreachError,
    ProfileError,
    SecurityError,
    StateMachineError,
    TailoringError,
)
from core.llm_client import ClaudeAPIClient, LLMClient, MockLLMClient
from core.security import decrypt_value, derive_key, detokenize_pii, encrypt_value, tokenize_pii
from core.state_machine import ALLOWED_TRANSITIONS, ApplicationState, transition_state
