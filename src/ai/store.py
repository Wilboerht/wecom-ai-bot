"""
Redis 持久化存储 — 会话、去重、限流、转接状态

若未配置 REDIS_URL，自动回退到内存模式（兼容单机部署）。
"""
import json
import logging
import time
from ..config import config

logger = logging.getLogger(__name__)

_redis = None


def _get_redis():
    """获取 Redis 连接（懒加载 + 连接池）"""
    global _redis
    if _redis is not None:
        return _redis

    url = getattr(config, "REDIS_URL", "") or ""
    if not url:
        return None

    try:
        import redis
        _redis = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3, decode_responses=True)
        _redis.ping()
        logger.info("Redis 连接成功: %s", url)
        return _redis
    except Exception as e:
        logger.warning("Redis 连接失败，使用内存模式: %s", e)
        _redis = False
        return None


# ---- 会话存储 ----
_inmem_sessions: dict[str, list[dict]] = {}
_inmem_session_ttl: dict[str, float] = {}

SESSION_PREFIX = "sess:"


def session_get(user_id: str) -> list[dict]:
    """获取会话历史"""
    r = _get_redis()
    if r:
        data = r.get(f"{SESSION_PREFIX}{user_id}")
        if data:
            return json.loads(data)
        return []

    # 内存模式
    _cleanup_sessions()
    return [dict(m) for m in _inmem_sessions.get(user_id, [])]


def session_add(user_id: str, role: str, content: str, max_msgs: int = 20) -> None:
    """追加消息到会话"""
    r = _get_redis()
    if r:
        key = f"{SESSION_PREFIX}{user_id}"
        data = r.get(key)
        msgs = json.loads(data) if data else []
        msgs.append({"role": role, "content": content})
        if len(msgs) > max_msgs:
            msgs = msgs[-max_msgs:]
        r.set(key, json.dumps(msgs, ensure_ascii=False), ex=config.SESSION_TTL)
        return

    # 内存模式
    if user_id not in _inmem_sessions:
        _inmem_sessions[user_id] = []
    _inmem_sessions[user_id].append({"role": role, "content": content})
    _inmem_session_ttl[user_id] = time.time()
    if len(_inmem_sessions[user_id]) > max_msgs:
        _inmem_sessions[user_id] = _inmem_sessions[user_id][-max_msgs:]


def _cleanup_sessions():
    """清理过期内存会话"""
    now = time.time()
    for uid in list(_inmem_session_ttl.keys()):
        if now - _inmem_session_ttl[uid] > config.SESSION_TTL:
            _inmem_sessions.pop(uid, None)
            _inmem_session_ttl.pop(uid, None)


# ---- 消息去重 ----
_inmem_msgids: set = set()
MSGID_PREFIX = "msgids:"


def msgid_is_duplicate(msg_id: str, open_kfid: str) -> bool:
    """检查消息是否已处理，未处理则标记"""
    r = _get_redis()
    if r:
        key = f"{MSGID_PREFIX}{open_kfid}"
        added = r.sadd(key, msg_id)
        r.expire(key, 1800)  # 30分钟
        return added == 0

    # 内存模式
    if msg_id in _inmem_msgids:
        return True
    _inmem_msgids.add(msg_id)
    if len(_inmem_msgids) > 500:
        _inmem_msgids = set(sorted(_inmem_msgids)[-200:])
    return False


# ---- 速率限制 ----
_inmem_ratelimit: dict[str, list[float]] = {}
RATELIMIT_PREFIX = "rl:"


def ratelimit_check(user_id: str, send_time: int = 0, max_per_window: int = 30, window_secs: int = 60) -> bool:
    """检查是否超限，返回 True 表示被限制"""
    now = time.time()
    if send_time and now - send_time > window_secs:
        return False

    r = _get_redis()
    if r:
        key = f"{RATELIMIT_PREFIX}{user_id}"
        cutoff = now - window_secs
        r.zremrangebyscore(key, "-inf", cutoff)
        count = r.zcard(key)
        if count >= max_per_window:
            return True
        r.zadd(key, {str(send_time or now): send_time or now})
        r.expire(key, window_secs * 2)
        return False

    # 内存模式
    timestamps = _inmem_ratelimit.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < window_secs]
    _inmem_ratelimit[user_id] = timestamps
    if len(timestamps) >= max_per_window:
        return True
    timestamps.append(send_time or now)
    return False


# ---- 转人工状态 ----
_inmem_transferred: set = set()
TRANSFER_PREFIX = "tf:"


def transfer_is_active(open_kfid: str, external_userid: str) -> bool:
    """检查用户是否已标记为转人工"""
    r = _get_redis()
    if r:
        return r.sismember(f"{TRANSFER_PREFIX}{open_kfid}", external_userid)

    return external_userid in _inmem_transferred


def transfer_mark(open_kfid: str, external_userid: str) -> None:
    """标记用户为转人工"""
    r = _get_redis()
    if r:
        key = f"{TRANSFER_PREFIX}{open_kfid}"
        r.sadd(key, external_userid)
        r.expire(key, 86400)  # 24小时
        return

    _inmem_transferred.add(external_userid)


def transfer_reset(open_kfid: str, external_userid: str) -> None:
    """取消转人工标记"""
    r = _get_redis()
    if r:
        r.srem(f"{TRANSFER_PREFIX}{open_kfid}", external_userid)
        return

    _inmem_transferred.discard(external_userid)
