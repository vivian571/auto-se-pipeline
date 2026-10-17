import io
import json
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from core.llm_provider import (
    BudgetExceededError,
    LLMResponse,
    OpenAICompatibleProvider,
    create_llm_provider,
)


def test_openai_compatible_provider_free_tier_cost():
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model_name="qwen2.5-coder:7b",
    )
    # 未在价格表中的本地开源模型，计费按 $0
    assert provider._calculate_cost(1000, 500) == 0.0


def test_openai_compatible_provider_priced_model():
    provider = OpenAICompatibleProvider(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        model_name="deepseek-chat",
    )
    # deepseek-chat: prompt $0.14/M, completion $0.28/M
    cost = provider._calculate_cost(1000, 1000)
    expected = (1000 * 0.14 / 1_000_000) + (1000 * 0.28 / 1_000_000)
    assert cost == pytest.approx(expected)


def test_openai_compatible_provider_budget_enforcement():
    provider = OpenAICompatibleProvider(max_tokens_budget=100)
    provider.total_tokens_consumed = 150
    with pytest.raises(BudgetExceededError):
        provider.check_budget()


@patch("urllib.request.urlopen")
def test_openai_compatible_provider_generate_success(mock_urlopen):
    # 模拟 API 成功响应
    mock_response_data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "def fix(): return True",
                }
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
        },
    }

    mock_resp_obj = MagicMock()
    mock_resp_obj.read.return_value = json.dumps(mock_response_data).encode("utf-8")
    mock_resp_obj.__enter__.return_value = mock_resp_obj
    mock_urlopen.return_value = mock_resp_obj

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        model_name="qwen2.5-coder:7b",
    )

    res = provider.generate(system_prompt="sys", user_prompt="user")

    assert res.content == "def fix(): return True"
    assert res.tokens_prompt == 50
    assert res.tokens_completion == 10
    assert res.cost_usd == 0.0
    assert provider.total_tokens_consumed == 60


@patch("urllib.request.urlopen")
def test_openai_compatible_provider_http_error(mock_urlopen):
    # 模拟 HTTP 401 鉴权失败
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="http://localhost/v1",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b'{"error": "invalid api key"}'),
    )

    provider = OpenAICompatibleProvider()
    with pytest.raises(RuntimeError) as exc_info:
        provider.generate("sys", "user")
    assert "LLM API request failed [401]" in str(exc_info.value)


def test_create_llm_provider_factory():
    local_p = create_llm_provider("local", base_url="http://my-host/v1")
    assert isinstance(local_p, OpenAICompatibleProvider)
    assert local_p.base_url == "http://my-host/v1"

    groq_p = create_llm_provider("groq", model_name="llama-3.3-70b-versatile")
    assert isinstance(groq_p, OpenAICompatibleProvider)

    mock_p = create_llm_provider("mock")
    assert not isinstance(mock_p, OpenAICompatibleProvider)
