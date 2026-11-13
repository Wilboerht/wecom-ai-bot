"""日志脱敏工具。

PII 规范要求日志只记录动作和截断后的标识，不记录完整 external_userid、
session_id、档案值或用户原文。所有面向日志的外部标识统一走这里。
"""
import re

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\r\n\t]")
_ID_RE = re.compile(r"^[0-9A-Za-z_\-]{1,128}$")


def mask_id(value: str, keep: int = 8) -> str:
    """外部用户/会话标识脱敏：只保留前缀，空值返回 '-'。"""
    text = str(value or "").strip()
    if not text:
        return "-"
    if not _ID_RE.fullmatch(text):
        # 非白名单内容（可能是攻击载荷）只返回固定占位，避免把原文写进日志
        return "<invalid-id>"
    if len(text) <= keep:
        return text[:2] + "***"
    return text[:keep] + "***"


def sanitize_log_value(value, max_len: int = 200) -> str:
    """清理日志字段：去控制字符/换行并截断，防止日志伪造。"""
    text = str(value if value is not None else "-")
    text = _CONTROL_RE.sub("?", text)
    return text[:max_len]
