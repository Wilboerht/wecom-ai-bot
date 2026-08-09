import logging
import traceback
from fastapi import Request
from fastapi.responses import Response

logger = logging.getLogger(__name__)


async def error_handler(request: Request, call_next):
    """全局异常处理中间件：任何未捕获异常都返回 HTTP 200 空包
    避免企业微信收到非 200 响应后发起重试风暴（5秒内最多3次）。
    错误详情记录到日志。"""
    try:
        return await call_next(request)
    except Exception:
        logger.error(
            "未处理异常 | path=%s | traceback:\n%s",
            request.url.path,
            traceback.format_exc(),
        )
        return Response(content="", status_code=200, media_type="text/plain")
