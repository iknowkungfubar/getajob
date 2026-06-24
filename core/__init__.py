"""GetAJob — Core framework.

Shared infrastructure: configuration, database, security, state machine,
event bus, LLM client, and exception hierarchy.
"""

from __future__ import annotations as _annotations

__all__ = [
    # Exceptions
    "GetAJobError",
    "ConfigurationError",
    "ProfileError",
    "IngestionError",
    "TailoringError",
    "BrowserError",
    "OutreachError",
    "StateMachineError",
    "SecurityError",
    # Config
    "GetAJobSettings",
    "load_config",
    # Database
    "Base",
    "create_engine",
    "get_session",
    # Security
    "encrypt_value",
    "decrypt_value",
    "tokenize_pii",
    "detokenize_pii",
    "derive_key",
    # State machine
    "ApplicationState",
    "ALLOWED_TRANSITIONS",
    "transition_state",
    # Event bus
    "EventBus",
    "EventPriority",
    "InMemoryEventBus",
    # LLM
    "LLMClient",
    "ClaudeAPIClient",
    "MockLLMClient",
]

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
from core.config import GetAJobSettings, load_config
from core.database import Base, create_engine, get_session
from core.security import decrypt_value, derive_key, encrypt_value, detokenize_pii, tokenize_pii
from core.state_machine import ALLOWED_TRANSITIONS, ApplicationState, transition_state
from core.event_bus import EventBus, EventPriority, InMemoryEventBus
from core.llm_client import LLMClient, ClaudeAPIClient, MockLLMClient
