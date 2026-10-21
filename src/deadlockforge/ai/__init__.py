"""AI coach package: universal LLM client + match analysis.

Public surface:
    - client:  create_client(), test_connection(), LLMError
    - coach:   analyze_match(), COACH_SYSTEM_PROMPT, heuristic_report
"""

from __future__ import annotations

from .client import (
    AnthropicClient,
    DeepSeekClient,
    LLMClient,
    LLMError,
    OpenAIClient,
    create_client,
    test_connection,
)
from .coach import COACH_SYSTEM_PROMPT, analyze_match, heuristic_report

__all__ = [
    "LLMClient",
    "LLMError",
    "DeepSeekClient",
    "OpenAIClient",
    "AnthropicClient",
    "create_client",
    "test_connection",
    "analyze_match",
    "heuristic_report",
    "COACH_SYSTEM_PROMPT",
]
