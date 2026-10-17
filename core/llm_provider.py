"""
LLM Provider and Budget Guardian for Auto-SE Pipeline
Abstracts model inference, enforces budget constraints, and tracks token costs.

Real providers (OpenAICompatible / Ollama / Groq / OpenRouter / DeepSeek)
use only the standard library (urllib) -- no new dependencies required.
API keys and base URLs are read securely from environment variables.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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


class OpenAICompatibleProvider(BaseLLMProvider):
    """
    Universal OpenAI-compatible provider using only the standard library.
    Works natively with:
    - Ollama (http://localhost:11434/v1)
    - Groq (https://api.groq.com/openai/v1)
    - OpenRouter (https://openrouter.ai/api/v1)
    - DeepSeek (https://api.deepseek.com/v1)
    - SiliconFlow, Moonshot, etc.
    """

    # 官方模型估算价格 (每 1M tokens 的 prompt / completion 美元价格)
    MODEL_PRICING: Dict[str, Dict[str, float]] = {
        "gpt-4o": {"prompt": 2.50, "completion": 10.00},
        "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
        "deepseek-chat": {"prompt": 0.14, "completion": 0.28},
        "deepseek-coder": {"prompt": 0.14, "completion": 0.28},
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_seconds: int = 120,
        **kwargs,
    ):
        super().__init__(**kwargs)
        # 优先使用显式传入参数，其次读取环境变量，最后回退至默认值
        self.base_url = (
            base_url
            or os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "ollama")
        self.model_name = (
            model_name
            or os.environ.get("LLM_MODEL", "qwen2.5-coder:7b")
        )
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        self.check_budget()

        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                resp_bytes = resp.read()
                resp_json = json.loads(resp_bytes.decode("utf-8"))

            content = resp_json["choices"][0]["message"]["content"]
            usage = resp_json.get("usage", {})

            prompt_tokens = usage.get("prompt_tokens") or (len(system_prompt + user_prompt) // 4)
            completion_tokens = usage.get("completion_tokens") or (len(content) // 4)

            # 计算费用：免费模型或未知模型按 $0 计算
            cost = self._calculate_cost(prompt_tokens, completion_tokens)

            self.total_tokens_consumed += (prompt_tokens + completion_tokens)
            self.total_cost_usd += cost

            return LLMResponse(
                content=content,
                tokens_prompt=prompt_tokens,
                tokens_completion=completion_tokens,
                cost_usd=cost,
                model_name=self.model_name,
            )

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM API request failed [{e.code}]: {error_body}") from e
        except Exception as e:
            raise RuntimeError(f"LLM API connection failed to {endpoint}: {str(e)}") from e

    def _calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = self.MODEL_PRICING.get(self.model_name)
        if not pricing:
            # 本地 Ollama, Groq 免费 tier 或自建模型按 $0 计费
            return 0.0

        p_rate = pricing["prompt"] / 1_000_000.0
        c_rate = pricing["completion"] / 1_000_000.0
        return (prompt_tokens * p_rate) + (completion_tokens * c_rate)


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


def create_llm_provider(
    provider_type: str = "mock",
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    **kwargs,
) -> BaseLLMProvider:
    """Factory function to instantiate the appropriate provider."""
    p = provider_type.lower()
    if p in ("local", "openai_compatible", "openai-compatible", "groq", "openrouter", "deepseek", "ollama"):
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            **kwargs,
        )
    return MockLLMProvider(**kwargs)
