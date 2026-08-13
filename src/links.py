"""
短链服务 — 长购买链接 → https://kf.nihplod.cn/l/{code} 映射

links.json 结构: { "code": "长链接" }
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://kf.nihplod.cn/l/{}"

_links: dict[str, str] = {}


def load_links(path: str = "links.json") -> dict[str, str]:
    """加载 code → 长链接 映射"""
    global _links
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _links = json.load(f)
        except Exception as e:
            logger.warning("links.json 加载失败: %s", e)
    return _links


def get_target(code: str) -> str:
    """根据短链 code 查目标长链接"""
    return _links.get(code, "")


def shorten_text(text: str, base: str = "") -> str:
    """把回复里的长链接替换成短链，未匹配的保持原样"""
    if not _links or not text:
        return text
    base = base or DEFAULT_BASE
    for code, url in _links.items():
        if url in text:
            text = text.replace(url, base.format(code))
    return text
