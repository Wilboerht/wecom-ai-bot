"""用户意图判定：转人工与政策咨询。"""
import re


def normalize_keywords(value) -> list[str]:
    """配置允许字符串或字符串数组；统一成列表，避免字符串被按单字迭代。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def transfer_requested(text: str, keywords: list | str) -> bool:
    """转人工判定：关键词子串匹配；消息整体就是「人工」也触发（隐私声明里写的触发词）

    裸「人工」不参与子串匹配（即使账号配置里带了也过滤掉），
    避免误伤「人工智能」「人工合成」等正常咨询
    """
    if text.strip() == "人工":
        return True
    return any(
        kw.strip() in text
        for kw in normalize_keywords(keywords)
        if kw.strip() != "人工"
    )


POLICY_QUESTION_PATTERNS = (
    "政策", "规则", "流程", "运费", "条件", "标准", "范围",
    "能不能", "可以吗", "能吗", "怎么退", "如何退", "怎么换", "如何换",
    "会过敏吗", "会不会过敏", "容易过敏吗",
)


def is_policy_question(text: str) -> bool:
    """退换货/过敏等政策咨询不应触发转人工，让 AI 按知识库回答"""
    if not any(k in text for k in ("退", "换", "过敏")):
        return False
    if any(p in text for p in POLICY_QUESTION_PATTERNS):
        return True
    return bool(re.search(r"[退换][^。！？]{0,8}吗", text))
