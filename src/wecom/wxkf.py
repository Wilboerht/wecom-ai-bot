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
from ..log_utils import mask_id

logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SYNC_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg"
SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg"
TRANSFER_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans"
STATE_GET_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/get"
APP_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
ACCOUNT_LINK_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/add_contact_way"
SEND_MSG_ON_EVENT_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg_on_event"

# 连接 3s / 读取 10s
_client = Client(
    timeout=httpx.Timeout(10.0, connect=3.0),
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
)

# access_token 缓存，按 corpid 分键: {corpid: {"value": str, "expires_at": float}}
_access_tokens: dict = {}
_token_lock = threading.Lock()

# token 失效类错误码（40014 token非法 / 42001 token过期 / 41001 缺少token）
TOKEN_INVALID_ERRCODES = (40014, 42001, 41001)
# 接口限频错误码
RATE_LIMIT_ERRCODE = 45009
# 网络异常重试退避间隔（最多重试 2 次）
RETRY_DELAYS = (0, 1, 3)


class WecomApiError(RuntimeError):
    """企业微信 API 调用失败（重试耗尽后抛出）"""


def get_access_token(corpid: str, secret: str, force_refresh: bool = False) -> str:
    """获取并缓存 access_token (线程安全，按 corpid 分键缓存)

    Raises:
        WecomApiError: 网络异常重试耗尽或接口返回错误
    """
    with _token_lock:
        now = time.time()
        entry = _access_tokens.get(corpid)
        if not force_refresh and entry and entry["value"] and entry["expires_at"] > now + 300:
            return entry["value"]

        token, expires_in = _fetch_access_token(corpid, secret)
        _access_tokens[corpid] = {"value": token, "expires_at": now + expires_in}
        logger.info("access_token 已刷新: corpid=%s", corpid)
        return token


def _fetch_access_token(corpid: str, secret: str) -> tuple:
    """向企微请求新 token，网络异常最多重试 2 次（退避 1s、3s）"""
    last_exc = None
    for attempt, delay in enumerate(RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            resp = _client.get(
                TOKEN_URL,
                params={"corpid": corpid, "corpsecret": secret},
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            last_exc = e
            # 不记录异常原文：httpx 异常可能带含 corpsecret 的请求 URL
            logger.warning("gettoken 网络/响应异常(第%d次): %s", attempt + 1, type(e).__name__)
            continue
        if not isinstance(data, dict):
            raise WecomApiError("获取access_token失败: 响应不是 JSON 对象")
        if data.get("errcode", 0) != 0:
            raise WecomApiError(f"获取access_token失败: {data}")
        return data["access_token"], data.get("expires_in", 7200)
    raise WecomApiError(f"gettoken 网络异常，重试耗尽: {type(last_exc).__name__}")


def _post_with_retry(url: str, access_token: str, payload: dict, action: str) -> dict:
    """POST 企微 API，网络异常最多重试 2 次（退避 1s、3s）

    Raises:
        WecomApiError: 网络异常重试耗尽
    """
    last_exc = None
    for attempt, delay in enumerate(RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            resp = _client.post(url, params={"access_token": access_token}, json=payload)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            last_exc = e
            logger.warning("%s 网络/响应异常(第%d次): %s", action, attempt + 1, type(e).__name__)
            continue
        if not isinstance(data, dict):
            last_exc = WecomApiError(f"{action} 响应不是 JSON 对象")
            logger.warning("%s 响应不是 JSON 对象(第%d次)", action, attempt + 1)
            continue
        return data
    raise WecomApiError(f"{action} 网络异常，重试耗尽: {type(last_exc).__name__}")


def _api_post(url: str, corpid: str, secret: str, payload: dict, action: str) -> dict:
    """POST 企微 API：token 失效刷新重试一次、限频退避重试一次、网络异常重试

    Returns:
        接口返回的 JSON（errcode == 0）

    Raises:
        WecomApiError: 重试耗尽或接口返回非 0 errcode
    """
    access_token = get_access_token(corpid, secret)
    data = _post_with_retry(url, access_token, payload, action)
    errcode = data.get("errcode", 0)

    if errcode in TOKEN_INVALID_ERRCODES:
        # token 失效：清缓存强制刷新，重试一次
        logger.warning("%s access_token 失效(errcode=%s)，强制刷新后重试", action, errcode)
        access_token = get_access_token(corpid, secret, force_refresh=True)
        data = _post_with_retry(url, access_token, payload, action)
        errcode = data.get("errcode", 0)

    if errcode == RATE_LIMIT_ERRCODE:
        # 接口限频：退避 1s 后重试一次
        logger.warning("%s 触发限频(errcode=45009)，1s 后重试", action)
        time.sleep(1)
        data = _post_with_retry(url, access_token, payload, action)
        errcode = data.get("errcode", 0)

    if errcode != 0:
        raise WecomApiError(f"{action} 失败: {data}")
    return data


def sync_messages(
    corpid: str,
    secret: str,
    token: str,
    open_kfid: str,
    cursor: str = "",
    limit: int = 1000,
) -> dict:
    """拉取客服消息（最近3天内的消息）

    Args:
        token: 回调事件中的 Token（10分钟有效）
        open_kfid: 客服账号 ID
        cursor: 分页游标，首次不传
        limit: 每次拉取条数 (默认1000，最大1000)

    Returns:
        {"msg_list": [...], "next_cursor": "...", "has_more": 0|1}

    Raises:
        WecomApiError: 接口调用失败（重试耗尽后抛出，调用方可区分"出错"与"无消息"）
    """
    payload = {
        "token": token,
        "open_kfid": open_kfid,
        "limit": limit,
        "cursor": cursor,
    }
    return _api_post(SYNC_MSG_URL, corpid, secret, payload, "sync_msg")


def send_message(
    corpid: str,
    secret: str,
    to_user: str,
    open_kfid: str,
    msgtype: str,
    content: str,
    msgid: str = "",
) -> bool:
    """发送消息给微信客服用户（当前仅支持 text）

    Args:
        to_user: 外部用户ID (external_userid)
        open_kfid: 客服账号ID
        msgtype: 消息类型，仅支持 "text"
        content: 文本消息内容
        msgid: 回复的消息ID（可选，用于引用回复）

    Returns:
        是否发送成功（失败返回 False 并记 error 日志，不抛异常）
    """
    assert msgtype == "text", f"send_message 仅支持 text 消息，当前: {msgtype}"

    payload = {
        "touser": to_user,
        "open_kfid": open_kfid,
        "msgtype": "text",
        "text": {"content": content},
    }

    if msgid:
        payload["msgid"] = msgid

    try:
        _api_post(SEND_MSG_URL, corpid, secret, payload, "send_msg")
    except WecomApiError as e:
        logger.error("send_msg 失败: %s", e)
        return False

    logger.info("send_msg 成功: to=%s", mask_id(to_user))
    return True


def transfer_session(
    corpid: str,
    secret: str,
    open_kfid: str,
    external_userid: str,
    service_userid: str = "",
    service_state: int = 3,
) -> bool:
    """变更会话状态，默认转为「由人工接待」(state=3)。state=1 转回智能助手

    Returns:
        是否成功（失败返回 False 并记 error 日志，不抛异常）
    """
    payload = {
        "open_kfid": open_kfid,
        "external_userid": external_userid,
        "service_state": service_state,
    }
    if service_state == 3 and service_userid:
        payload["servicer_userid"] = service_userid

    try:
        _api_post(TRANSFER_URL, corpid, secret, payload, "transfer_session")
    except WecomApiError as e:
        logger.error("transfer_session 失败: %s", e)
        return False

    logger.info("transfer_session 成功: to=%s", service_userid)
    return True


def get_session_state(corpid: str, secret: str, open_kfid: str, external_userid: str) -> int:
    """查询微信客服会话状态: 0=未处理 1=智能助手 2=排队 3=人工接待 4=已结束

    Raises:
        WecomApiError: 接口调用失败（重试耗尽后抛出，不再静默返回 -1）
    """
    data = _api_post(STATE_GET_URL, corpid, secret,
                     {"open_kfid": open_kfid, "external_userid": external_userid},
                     "get_session_state")
    return data.get("service_state", 0)


def get_account_link(corpid: str, secret: str, open_kfid: str, scene: str = "") -> str:
    """获取客服账号链接（带场景值），返回的 url 可追加 scene_param=xxx 参数

    注意：返回的链接不允许修改或复制参数到其他链接使用，否则进入会话事件
    参数校验不通过，无法回调。失败返回空字符串并记 error 日志。
    """
    payload = {"open_kfid": open_kfid}
    if scene:
        payload["scene"] = scene

    try:
        data = _api_post(ACCOUNT_LINK_URL, corpid, secret, payload, "add_contact_way")
    except WecomApiError as e:
        logger.error("add_contact_way 失败: %s", e)
        return ""

    url = data.get("url", "")
    if not url:
        logger.error("add_contact_way 返回空 url: %s", data)
    return url


def send_msg_on_event(corpid: str, secret: str, code: str, content: str) -> bool:
    """发送欢迎语等事件响应消息（enter_session 的 welcome_code 等）

    Returns:
        是否发送成功（失败返回 False 并记 error 日志，不抛异常）
    """
    if not code or not content:
        return False

    payload = {
        "code": code,
        "msgtype": "text",
        "text": {"content": content},
    }

    try:
        _api_post(SEND_MSG_ON_EVENT_URL, corpid, secret, payload, "send_msg_on_event")
    except WecomApiError as e:
        logger.error("send_msg_on_event 失败: %s", e)
        return False
    return True


def send_agent_notice(corpid: str, secret: str, agent_id: str, to_user: str, content: str) -> bool:
    """给客服人员发企业微信应用消息（转人工上下文通知，客户不可见）

    Returns:
        是否发送成功（失败返回 False 并记 error 日志，不抛异常）
    """
    if not agent_id or not to_user:
        logger.warning("send_agent_notice 跳过: agent_id=%s to_user=%s", agent_id, to_user)
        return False

    try:
        agent_id_int = int(agent_id)
    except (ValueError, TypeError):
        logger.error("send_agent_notice 失败: agent_id 非数字: %r", agent_id)
        return False

    payload = {
        "touser": to_user,
        "msgtype": "text",
        "agentid": agent_id_int,
        "text": {"content": content},
    }

    try:
        _api_post(APP_MSG_URL, corpid, secret, payload, "send_agent_notice")
    except WecomApiError as e:
        logger.error("send_agent_notice 失败: %s", e)
        return False

    logger.info("send_agent_notice 成功: to=%s", to_user)
    return True
