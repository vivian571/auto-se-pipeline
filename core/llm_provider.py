"""
LLM Provider and Budget Guardian for Auto-SE Pipeline
Abstracts model inference, enforces budget constraints, and tracks token costs.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class BudgetExceededError(Exception):
    """Raised when a task exceeds its configured token or monetary budget."""
    pass


@dataclass
class LLMResponse:
    content: str
    tokens_prompt: int
    tokens_completion: int
    cost_usd: float
    model_name: str


class BaseLLMProvider(ABC):
    def __init__(
        self,
        max_tokens_budget: int = 150000,
        max_cost_usd_budget: float = 1.50,
    ):
        self.max_tokens_budget = max_tokens_budget
        self.max_cost_usd_budget = max_cost_usd_budget
        self.total_tokens_consumed = 0
        self.total_cost_usd = 0.0

    def check_budget(self) -> None:
        if self.total_tokens_consumed >= self.max_tokens_budget:
            raise BudgetExceededError(
                f"Task exceeded token budget: {self.total_tokens_consumed} >= {self.max_tokens_budget}"
            )
        if self.total_cost_usd >= self.max_cost_usd_budget:
            raise BudgetExceededError(
                f"Task exceeded cost budget: ${self.total_cost_usd:.4f} >= ${self.max_cost_usd_budget:.2f}"
            )

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        pass


class MockLLMProvider(BaseLLMProvider):
    """Deterministic mock provider for unit tests and offline replay benchmarks."""

    def __init__(self, canned_responses: Optional[List[str]] = None, **kwargs):
        super().__init__(**kwargs)
        self.canned_responses = canned_responses or []
        self._call_count = 0

    def set_responses(self, responses: List[str]) -> None:
        self.canned_responses = responses
        self._call_count = 0

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        self.check_budget()

        if self._call_count < len(self.canned_responses):
            resp_text = self.canned_responses[self._call_count]
        else:
            resp_text = "# Mock Default Output"

        self._call_count += 1
        prompt_tokens = len(system_prompt + user_prompt) // 4
        comp_tokens = len(resp_text) // 4
        cost = (prompt_tokens * 0.000003) + (comp_tokens * 0.000015)

        self.total_tokens_consumed += (prompt_tokens + comp_tokens)
        self.total_cost_usd += cost

        return LLMResponse(
            content=resp_text,
            tokens_prompt=prompt_tokens,
            tokens_completion=comp_tokens,
            cost_usd=cost,
            model_name="mock-model",
        )
