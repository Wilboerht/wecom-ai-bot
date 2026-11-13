"""人工客服服务时段领域规则。"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import config

logger = logging.getLogger(__name__)


def service_hour(name: str, default: int) -> int:
    """读取服务时间配置：None 用默认值；非法值记 warning 落默认。
    不能用 `or` 兜底——0 是合法值（如 SERVICE_START_HOUR=0 表示全天从 0 点开始）
    """
    raw = getattr(config, name, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("%s 配置非法(%r)，使用默认值 %s", name, raw, default)
        return default


def is_within_service_hours() -> bool:
    """检查当前是否在人工服务时间（默认周一至周五 10:00-20:00，中国时区）"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.weekday() >= 5:
        return False
    start_hour = service_hour("SERVICE_START_HOUR", 10)
    end_hour = service_hour("SERVICE_END_HOUR", 20)
    return not (now.hour < start_hour or now.hour >= end_hour)


def service_hours_line() -> str:
    """当前人工客服服务时间（.env 可配），避免知识库/固定文案写死后与实际不一致。"""
    start = service_hour("SERVICE_START_HOUR", 10)
    end = service_hour("SERVICE_END_HOUR", 20)
    return f"人工客服服务时间：周一至周五 {start:02d}:00-{end:02d}:00（北京时间）。"
