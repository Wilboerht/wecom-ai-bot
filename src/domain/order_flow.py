"""订单查询引导流程的解析规则（状态机在 core.KFProcessor 中编排）。"""
import re

ORDER_FLOW_TTL = 600
ORDER_FLOW_EXIT_WORDS = ("算了", "不用了", "取消")
ORDER_FLOW_MAX_ATTEMPTS = 3
ORDER_FLOW_CHANNEL_MAX_LEN = 20
ORDER_TRIGGERS = (
    "查订单", "查询订单", "查单", "订单号", "订单进度",
    "查物流", "物流信息", "快递单号", "快递到哪", "物流到哪",
)

# 独立成号的 11 位手机号（1[3-9] 开头，前后无其他数字）：
# 兜底匹配长数字前先剔除，避免手机号被误判为订单号
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _bare_order_no_search(text: str) -> re.Match | None:
    """剔除独立手机号后匹配 10 位以上纯数字"""
    return re.search(r"\d{10,}", _PHONE_RE.sub("", text))


def _extract_order_no(text: str) -> str:
    """从文本提取订单号：带「订单号/单号」前缀的6-32位字母数字，或10位以上纯数字（排除11位手机号）"""
    m = re.search(r"(?:订单号|单号)[:：\s]*([A-Za-z0-9\-]{6,32})", text)
    if m:
        return m.group(1)
    m = _bare_order_no_search(text)
    return m.group(0) if m else ""


def _split_channel_order(text: str) -> tuple[str, str]:
    """渠道回复里可能同时带订单号，拆成 (渠道, 订单号)"""
    order_no = _extract_order_no(text)
    if not order_no:
        return text.strip()[:50], ""
    m = re.search(r"(?:订单号|单号)[:：\s]*([A-Za-z0-9\-]{6,32})", text) or _bare_order_no_search(text)
    channel = (text[:m.start()] + text[m.end():]).strip(" ,，。.")
    return channel[:50], order_no


def _order_triggered(text: str) -> bool:
    if any(t in text for t in ORDER_TRIGGERS):
        return True
    p1 = re.search(r"(查询|查|看|找|跟踪).{0,8}(订单|物流|快递)", text)
    p2 = re.search(r"(订单|物流|快递).{0,8}(查询|查|看|找)", text)
    return bool(p1 or p2)
