"""Agents for the GetAJob Hermes-compatible agent system.

Every agent inherits from :class:`~agents.base.BaseAgent` and implements
a :meth:`~agents.base.BaseAgent.run` coroutine that the orchestrator invokes.

Usage::

    from agents import IngestionAgent, ContextAgent
"""

from __future__ import annotations as _annotations

__all__: list[str] = [
    "BaseAgent",
    "ContextAgent",
    "HumanInLoopPause",
    "IngestionAgent",
    "OrchestratorAgent",
    "TailoringAgent",
]

from agents.base import BaseAgent, HumanInLoopPause
from agents.context_agent import ContextAgent
from agents.ingestion_agent import IngestionAgent
from agents.orchestrator_agent import OrchestratorAgent
from agents.tailoring_agent import TailoringAgent
