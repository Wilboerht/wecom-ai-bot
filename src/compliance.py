"""
广告法合规后置兜底 — AI 输出的禁语检查与替换

禁语表与 knowledge.txt 的「广告法禁语（强制遵守）」一节保持一致。
prompt/知识库已要求模型自律，本模块是输出侧的最后防线：
命中禁语时替换为合规表述并记 warning，绝不放行原文。
"""
import logging
import re

logger = logging.getLogger(__name__)

# 禁语 → 合规替代词。与 knowledge.txt 同步维护；新增禁语在此登记即可生效。
# 替换按词长降序执行，避免短词先替换破坏长词（如「当天见效」先于「见效快」）。
BANNED_REPLACEMENTS: dict[str, str] = {
    "当天见效": "坚持使用有助于看到变化",
    "永不反弹": "长期坚持有助于维持好状态",
    "无效退款": "如有问题可联系售后咨询",
    "见效快": "坚持使用有助于改善",
    "生长因子": "修护成分",
    "抗老": "抗初老",
    "抗皱": "改善细纹",
    "除皱": "改善细纹",
    "祛斑": "提亮",
    "美白": "提亮",
    "治愈": "辅助改善",
    "治疗": "修护",
    "消炎": "舒缓",
    "杀菌": "清洁",
    "排毒": "调理",
    "根治": "改善",
    "百分百": "很大程度",
    "绝对": "非常",
    "永久": "持久",
    "顶级": "高端",
    # 「第一」仅在宣称语境替换：排除「第一步/第一次」等序数用法（负向前瞻）
    "第一(?![步次个天条])": "前列",
}

# 编译为 (正则, 替代词, 禁语标签)；按键长降序，避免短词先替换破坏长词（如「当天见效」先于「见效快」）
def _label(pat: str) -> str:
    """日志用禁语标签：取键的前导中文部分（剥掉正则语法）"""
    m = re.match(r"[\u4e00-\u9fff]+", pat)
    return m.group(0) if m else pat


_ORDERED = [
    (re.compile(pat), good, _label(pat))
    for pat, good in sorted(BANNED_REPLACEMENTS.items(), key=lambda kv: len(kv[0]), reverse=True)
]


def sanitize_reply(text: str) -> tuple[str, list[str]]:
    """输出侧禁语兜底：把命中的禁语替换为合规表述。

    Returns:
        (处理后文本, 命中的禁语列表)；未命中时原文原样返回、列表为空。
    """
    if not text:
        return text, []
    hits: list[str] = []
    for pattern, good, label in _ORDERED:
        if pattern.search(text):
            hits.append(label)
            text = pattern.sub(good, text)
    return text, hits
