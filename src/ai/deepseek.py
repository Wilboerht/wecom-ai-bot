import logging
from httpx import Client, HTTPStatusError, TimeoutException
from ..config import config

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


class AIServiceError(Exception):
    """AI 服务不可用"""
    pass


def chat(messages: list[dict], timeout: float = 28.0) -> str:
    """调用 DeepSeek API 进行对话

    Raises:
        AIServiceError: AI 服务不可用时抛出，调用方应转人工处理
    """
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": 2000,
        "temperature": 0.6,
    }

    try:
        with Client(timeout=timeout) as client:
            resp = client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    except HTTPStatusError as e:
        status = e.response.status_code
        logger.error(f"DeepSeek API HTTP {status}")
        raise AIServiceError(f"HTTP {status}") from e

    except TimeoutException as e:
        logger.error("DeepSeek API 请求超时")
        raise AIServiceError(f"timeout") from e

    except Exception as e:
        logger.error(f"DeepSeek API 调用异常: {e}")
        raise AIServiceError(str(e)) from e
