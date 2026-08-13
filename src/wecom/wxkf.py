"""
微信客服 API 模块

消息流程:
  回调事件(XML) → 提取 Token → sync_msg 拉取(JSON) → 处理 → send_msg 发送(JSON)
"""
import logging
import threading
import time
import httpx
from httpx import Client

logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SYNC_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg"
SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg"
TRANSFER_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans"
STATE_GET_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/get"
APP_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"

_client = Client(timeout=10, limits=httpx.Limits(max_keepalive_connections=5, max_connections=20))

_access_token: dict = {"value": "", "expires_at": 0}
_token_lock = threading.Lock()


def get_access_token(corpid: str, secret: str) -> str:
    """获取并缓存 access_token (线程安全)"""
    with _token_lock:
        now = time.time()
        if _access_token["value"] and _access_token["expires_at"] > now + 300:
            return _access_token["value"]

        resp = _client.get(
            TOKEN_URL,
            params={"corpid": corpid, "corpsecret": secret},
        )
        data = resp.json()

        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"获取access_token失败: {data}")

        _access_token["value"] = data["access_token"]
        _access_token["expires_at"] = now + data.get("expires_in", 7200)
        logger.info("access_token 已刷新")
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

    resp = _client.post(SYNC_MSG_URL, params={"access_token": access_token}, json=payload)
    data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"sync_msg 失败: {data}")
        return {"msg_list": [], "next_cursor": "", "has_more": 0}

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

    resp = _client.post(SEND_MSG_URL, params={"access_token": access_token}, json=payload)
    data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"send_msg 失败: {data}")
        return False

    logger.info("send_msg 成功: to=%s", to_user)
    return True


def transfer_session(
    corpid: str,
    secret: str,
    open_kfid: str,
    external_userid: str,
    service_userid: str = "",
    service_state: int = 3,
) -> bool:
    """变更会话状态，默认转为「由人工接待」(state=3)。state=1 转回智能助手"""
    access_token = get_access_token(corpid, secret)

    payload = {
        "open_kfid": open_kfid,
        "external_userid": external_userid,
        "service_state": service_state,
    }
    if service_state == 3 and service_userid:
        payload["servicer_userid"] = service_userid

    resp = _client.post(TRANSFER_URL, params={"access_token": access_token}, json=payload)
    data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"transfer_session 失败: {data}")
        return False

    logger.info("transfer_session 成功: to=%s", service_userid)
    return True


def get_session_state(corpid: str, secret: str, open_kfid: str, external_userid: str) -> int:
    """查询微信客服会话状态: 0=未处理 1=智能助手 2=排队 3=人工接待 4=已结束"""
    access_token = get_access_token(corpid, secret)
    resp = _client.post(STATE_GET_URL, params={"access_token": access_token},
                        json={"open_kfid": open_kfid, "external_userid": external_userid})
    data = resp.json()
    if data.get("errcode", 0) != 0:
        logger.error(f"get_session_state 失败: {data}")
        return -1
    return data.get("service_state", 0)


def send_agent_notice(corpid: str, secret: str, agent_id: str, to_user: str, content: str) -> bool:
    """给客服人员发企业微信应用消息（转人工上下文通知，客户不可见）"""
    if not agent_id or not to_user:
        logger.warning("send_agent_notice 跳过: agent_id=%s to_user=%s", agent_id, to_user)
        return False

    access_token = get_access_token(corpid, secret)
    payload = {
        "touser": to_user,
        "msgtype": "text",
        "agentid": int(agent_id),
        "text": {"content": content},
    }
    resp = _client.post(APP_MSG_URL, params={"access_token": access_token}, json=payload)
    data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"send_agent_notice 失败: {data}")
        return False

    logger.info("send_agent_notice 成功: to=%s", to_user)
    return True
