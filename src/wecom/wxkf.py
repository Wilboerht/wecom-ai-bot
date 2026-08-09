"""
微信客服 API 模块

消息流程:
  回调事件(XML) → 提取 Token → sync_msg 拉取(JSON) → 处理 → send_msg 发送(JSON)
"""
import logging
import time
from httpx import Client

logger = logging.getLogger(__name__)

TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SYNC_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg"
SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg"
TRANSFER_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans"

_access_token: dict = {"value": "", "expires_at": 0}


def get_access_token(corpid: str, secret: str) -> str:
    """获取并缓存 access_token (有效期7200秒, 提前5分钟刷新)"""
    now = time.time()
    if _access_token["value"] and _access_token["expires_at"] > now + 300:
        return _access_token["value"]

    with Client(timeout=10) as client:
        resp = client.get(
            TOKEN_URL,
            params={"corpid": corpid, "corpsecret": secret},
        )
        data = resp.json()

    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"获取access_token失败: {data}")

    _access_token["value"] = data["access_token"]
    _access_token["expires_at"] = now + data.get("expires_in", 7200)
    logger.info("access_token 已刷新, 过期时间: %.0f秒后", data.get("expires_in", 7200))
    return _access_token["value"]


def sync_messages(
    corpid: str,
    secret: str,
    token: str,
    open_kfid: str,
    cursor: str = "",
    limit: int = 100,
) -> dict:
    """拉取客服消息（最近3天内的消息）

    Args:
        token: 回调事件中的 Token（10分钟有效）
        open_kfid: 客服账号 ID
        cursor: 分页游标，首次不传
        limit: 每次拉取条数 (默认1000，最大1000)

    Returns:
        {"msg_list": [...], "next_cursor": "...", "has_more": 0|1}
    """
    access_token = get_access_token(corpid, secret)

    payload = {
        "token": token,
        "open_kfid": open_kfid,
        "limit": limit,
        "cursor": cursor,
    }

    with Client(timeout=10) as client:
        resp = client.post(
            f"{SYNC_MSG_URL}?access_token={access_token}",
            json=payload,
        )
        data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"sync_msg 失败: {data}")
        return {"msg_list": [], "next_cursor": "", "has_more": 0, "_error": data}

    return data


def send_message(
    corpid: str,
    secret: str,
    to_user: str,
    open_kfid: str,
    msgtype: str,
    content: str,
    msgid: str = "",
) -> bool:
    """发送消息给微信客服用户

    Args:
        to_user: 外部用户ID (external_userid)
        open_kfid: 客服账号ID
        msgtype: 消息类型 (text/image/voice/...)
        content: 文本消息内容（仅 msgtype=text 时有效）
        msgid: 回复的消息ID（可选，用于引用回复）

    Returns:
        是否发送成功
    """
    access_token = get_access_token(corpid, secret)

    payload = {
        "touser": to_user,
        "open_kfid": open_kfid,
        "msgtype": msgtype,
    }

    if msgtype == "text":
        payload["text"] = {"content": content}

    if msgid:
        payload["msgid"] = msgid

    with Client(timeout=10) as client:
        resp = client.post(
            f"{SEND_MSG_URL}?access_token={access_token}",
            json=payload,
        )
        data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"send_msg 失败: {data}")
        return {"ok": False, "_error": data}

    logger.info("send_msg 成功: to=%s", to_user)
    return {"ok": True}


def transfer_session(
    corpid: str,
    secret: str,
    open_kfid: str,
    external_userid: str,
    service_userid: str,
) -> bool:
    """将会话状态变更为「由人工接待」

    Args:
        open_kfid: 客服账号ID
        external_userid: 外部用户ID
        service_userid: 接待人员的企微userid

    Returns:
        是否转移成功
    """
    access_token = get_access_token(corpid, secret)

    payload = {
        "open_kfid": open_kfid,
        "external_userid": external_userid,
        "service_state": 3,
        "servicer_userid": service_userid,
    }

    with Client(timeout=10) as client:
        resp = client.post(
            f"{TRANSFER_URL}?access_token={access_token}",
            json=payload,
        )
        data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"transfer_session 失败: {data}")
        return False

    logger.info("transfer_session 成功: to=%s", service_userid)
    return True
