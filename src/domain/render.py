"""回复渲染：Markdown 清理、长文本按 UTF-8 字节拆条、企微发送。"""
import logging
import re

from ..config import config
from ..log_utils import mask_id

logger = logging.getLogger(__name__)

# 企微客服 send_msg 文本上限 2048 字节（UTF-8 中文 3 字节≈680 字）；
# 预留 buffer 避免临界抖动，超过则按行边界拆分多条发送
_KF_MAX_BYTES = 2000


def strip_markdown(text: str) -> str:
    """硬性清理 Markdown 语法，保证发给微信的是纯文本"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)
    # 去掉数字列表符号 1. 2. 3.（排除小数：1.5倍/2.5% 不能被截成 5倍/5%）
    text = re.sub(r'^[ \t]*\d+[.、](?!\d)\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'`{1,3}', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def hard_split_by_bytes(line: str) -> list[str]:
    """按 UTF-8 字节上限硬切单行；4 字节 emoji/扩展汉字也不会超限。"""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for ch in line:
        ch_size = len(ch.encode("utf-8"))
        if buf and size + ch_size > _KF_MAX_BYTES:
            chunks.append("".join(buf))
            buf = []
            size = 0
        buf.append(ch)
        size += ch_size
    if buf:
        chunks.append("".join(buf))
    return chunks


def split_kf_content(content: str) -> list[str]:
    """把超长回复拆成多条不超 _KF_MAX_BYTES 字节的消息；优先按换行拆，单行超限按字节硬切。"""
    if len(content.encode("utf-8")) <= _KF_MAX_BYTES:
        return [content]
    chunks: list[str] = []
    current = ""
    for line in content.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate.encode("utf-8")) <= _KF_MAX_BYTES:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line.encode("utf-8")) > _KF_MAX_BYTES:
            pieces = hard_split_by_bytes(line)
            chunks.extend(pieces[:-1])
            current = pieces[-1] if pieces else ""
        else:
            current = line
    if current:
        chunks.append(current)
    return chunks


def send_kf_text(open_kfid: str, to_user: str, content: str, msgid: str = "") -> bool:
    """发送客服文本消息，返回是否成功；超长自动拆条，msgid 只挂第一条。"""
    from ..wecom.wxkf import send_message

    parts = split_kf_content(content)
    for i, part in enumerate(parts):
        ok = send_message(
            corpid=config.WECOM_CORP_ID,
            secret=config.WECOM_SECRET,
            to_user=to_user,
            open_kfid=open_kfid,
            msgtype="text",
            content=part,
            msgid=msgid if i == 0 else "",
        )
        if not ok:
            logger.error(
                "send_msg 失败: open_kfid=%s to=%s 分片=%d/%d",
                open_kfid, mask_id(to_user), i + 1, len(parts),
            )
            return False
    return True
