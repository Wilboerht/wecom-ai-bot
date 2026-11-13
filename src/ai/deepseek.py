import logging
import threading
import time
from httpx import Client, HTTPStatusError, TimeoutException, TransportError
from ..config import config
from .. import metrics

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# 最多 2 次重试的指数退避秒数（1s、3s）
_RETRY_BACKOFF = (1, 3)

# 模块级共享 httpx.Client：连接复用，线程安全（httpx.Client 可跨线程复用）
_client: Client | None = None
_client_lock = threading.Lock()


def _get_client() -> Client:
    """获取共享 HTTP 客户端（懒加载，双检锁保证只建一次）"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = Client()
    return _client


class AIServiceError(Exception):
    """AI 服务不可用"""
    pass


def chat(messages: list[dict], timeout: float = 28.0, max_retries: int = 2) -> str:
    """调用 DeepSeek API 进行对话

    对 HTTP 429、5xx、超时/网络异常最多重试 max_retries 次（指数退避 1s、3s），
    重试耗尽才抛 AIServiceError；4xx（非 429）不重试。
    公众号被动回复等硬时限场景用 max_retries=0 + 短 timeout，
    保证总耗时可控（退避 sleep 会超出平台响应时限）。

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
        "max_tokens": config.DEEPSEEK_MAX_TOKENS,
        # 采样温度 .env 可配（DEEPSEEK_TEMPERATURE，默认 0.3）：低值更确定，红线/合规场景更稳
        "temperature": config.DEEPSEEK_TEMPERATURE,
    }

    backoffs = _RETRY_BACKOFF[:max(0, max_retries)]
    max_attempts = len(backoffs) + 1
    for attempt in range(max_attempts):
        if attempt > 0:
            backoff = backoffs[attempt - 1]
            logger.warning("DeepSeek API 第 %d 次重试（%ds 退避后）", attempt, backoff)
            time.sleep(backoff)
        try:
            resp = _get_client().post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            usage = data.get("usage") or {}
            logger.info(
                "DeepSeek token 用量: prompt_tokens=%s completion_tokens=%s",
                usage.get("prompt_tokens"), usage.get("completion_tokens"),
            )
            if choice.get("finish_reason") == "length":
                # 回复被 max_tokens 截断：内容已残缺（护肤方案可能少半截），计数并告警便于调整上限
                metrics.inc(metrics.AI_TRUNCATED)
                logger.warning("DeepSeek 回复被 max_tokens 截断: completion_tokens=%s", usage.get("completion_tokens"))
            content = (choice.get("message") or {}).get("content")
            if not isinstance(content, str) or not content.strip():
                # content filter / 空回复：不能把空字符串发给用户，走 AI 故障兜底
                metrics.inc(metrics.AI_EMPTY_RESPONSE)
                raise AIServiceError("empty response")
            return content

        except HTTPStatusError as e:
            status = e.response.status_code
            if (status == 429 or status >= 500) and attempt < max_attempts - 1:
                logger.warning("DeepSeek API HTTP %d，准备重试", status)
                continue
            logger.error(f"DeepSeek API HTTP {status}")
            raise AIServiceError(f"HTTP {status}") from e

        except (TimeoutException, TransportError) as e:
            if attempt < max_attempts - 1:
                logger.warning("DeepSeek API 请求超时/网络异常: %s，准备重试", e)
                continue
            logger.error("DeepSeek API 请求超时/网络异常: %s", e)
            raise AIServiceError(str(e) or "timeout") from e

        except Exception as e:
            logger.error(f"DeepSeek API 调用异常: {e}")
            raise AIServiceError(str(e)) from e
