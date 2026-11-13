"""
短链服务 — 长购买链接 → https://kf.nihplod.cn/l/{code} 映射

links.json 结构: { "code": "长链接" }
按 mtime 热加载：修改 links.json 后无需重启即可生效。
"""
import json
import logging
import os
import re
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://kf.nihplod.cn/l/{}"

_links: dict[str, str] = {}
_links_path = ""             # 最近一次 load_links 使用的路径，供热加载比对
_links_fingerprint = None    # (mtime, size)；双指纹防止同秒写入读到半截文件


def load_links(path: str = "links.json") -> dict[str, str]:
    """加载 code → 长链接 映射；加载失败保留上一份成功加载的缓存"""
    global _links, _links_path, _links_fingerprint
    _links_path = path
    if not os.path.exists(path):
        logger.error("链接配置不存在: %s（沿用现有缓存 %d 条）", path, len(_links))
        return _links
    try:
        st = os.stat(path)
        fingerprint = (st.st_mtime, st.st_size)
        if fingerprint == _links_fingerprint:
            return _links  # 文件未变，沿用缓存
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 合法 JSON 但顶层不是对象（如 []）时按加载失败处理，
        # 否则 _links 被污染后 shorten_text 的 .items() 会反复抛 AttributeError
        if not isinstance(data, dict):
            raise ValueError("links.json 顶层必须是对象 (code → 长链接)")
        valid = {}
        for code, url in data.items():
            if isinstance(code, str) and isinstance(url, str) and url.startswith(("http://", "https://")):
                valid[code] = url
            else:
                logger.warning("links.json 条目非法已忽略: code=%r", code)
        _links = valid
        _links_fingerprint = fingerprint
    except Exception as e:
        logger.error("links.json 加载失败: %s（沿用现有缓存 %d 条）", e, len(_links))
    return _links


def _reload_if_changed() -> None:
    """读取前比对 mtime/size，links.json 变更则热加载"""
    if not _links_path or not os.path.exists(_links_path):
        return
    try:
        st = os.stat(_links_path)
    except OSError:
        return
    if (st.st_mtime, st.st_size) != _links_fingerprint:
        load_links(_links_path)


def get_target(code: str) -> str:
    """根据短链 code 查目标长链接"""
    _reload_if_changed()
    return _links.get(code, "")


# 匹配约定：LLM 改写回复时可能改动/截断链接中的时效参数（xsec_token、apptime、
# share_id、tk 等），整串精确匹配会失败，因此按稳定标识生成正则：
#   - query 中带 id=<数字>（如抖音商品链接）：按 域名 + id 值 匹配，其余参数任意
#   - 路径本身携带标识（如小红书 goods-detail/<商品id>、天猫 e.tb.cn/h.<code>）：
#     按 域名 + 路径前缀 匹配，query 任意
# 链接终止字符约定为空白、方括号与中英文逗号/句号/括号（知识库中链接形如 [平台:URL]）。
_URL_TAIL = r"[^\s\],，。）)]*"


def _url_patterns(url: str) -> list[re.Pattern]:
    """从长链接提取稳定标识，生成匹配正则（匹配约定见上方注释）"""
    p = urlparse(url)
    ids = parse_qs(p.query).get("id")
    if ids and ids[0].isdigit():
        # 商品 id 在 query 中（如抖音）：路径对所有商品相同，只按 id 值区分
        # (?!\d) 防止 id 互为前缀时误配
        return [re.compile(
            rf"https?://{re.escape(p.netloc)}/{_URL_TAIL}[?&]id={re.escape(ids[0])}(?!\d){_URL_TAIL}"
        )]
    if p.path.strip("/"):
        # 路径携带标识（小红书商品 id / 天猫短链 code）：query 允许任意改写
        return [re.compile(
            rf"https?://{re.escape(p.netloc)}{re.escape(p.path)}{_URL_TAIL}"
        )]
    # 无稳定标识可提取时退化为整串精确匹配
    return [re.compile(re.escape(url))]


def shorten_text(text: str, base: str = "") -> str:
    """把回复里的长链接替换成短链，未匹配的保持原样"""
    _reload_if_changed()
    if not _links or not text:
        return text
    base = base or DEFAULT_BASE
    for code, url in _links.items():
        for pat in _url_patterns(url):
            text = pat.sub(base.format(code), text)
    return text
