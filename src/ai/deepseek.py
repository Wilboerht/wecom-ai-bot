import logging
from httpx import Client, HTTPStatusError, TimeoutException
from ..config import config

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


def chat(messages: list[dict], timeout: float = 28.0) -> str:
    """调用 DeepSeek API 进行对话

    Args:
        messages: OpenAI 格式的消息列表
        timeout: 请求超时秒数

    Returns:
        AI 回复文本
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

        if status == 401:
            return "服务配置错误，请联系管理员。"
        if status == 429:
            return "当前咨询人数较多，请稍后再试。"
        if status == 402:
            return "服务暂时不可用（账户余额不足），请联系管理员。"

        return "抱歉，AI服务暂时不可用，请稍后再试。"

    except TimeoutException:
        logger.error("DeepSeek API 请求超时")
        return "AI正在思考中，请稍后再发消息试试。"

    except Exception as e:
        logger.error(f"DeepSeek API 调用异常: {e}")
        return "抱歉，AI服务暂时不可用，请稍后再试。"
