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
    """追加消息到会话。媒体类内容（图片/语音）应只传元信息，不传原始二进制"""
    # 安全校验：拒绝超长内容（可能是误传的 base64 图片数据）
    if len(content) > 4096:
        logger.warning("session_add rejected: content too long (%d chars) — possible media data leak", len(content))
        content = content[:200] + "...[truncated]"
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


# ---- 轮流分配计数器 ----
_RR_PREFIX = "rr:"


def transfer_next_index(open_kfid: str, total: int) -> int:
    """获取下次转接应该分配给第几个人（轮轮流），Redis原子递增"""
    if total <= 1:
        return 0
    r = _get_redis()
    if r:
        idx = r.incr(f"{_RR_PREFIX}{open_kfid}") % total
        if idx < 0:
            idx = 0
        return idx
    # 内存回退：简单轮转
    import random
    return random.randrange(total)


# ---- 接待工作量均衡（最少接待优先） ----
_LOAD_PREFIX = "load:"


def transfer_pick_agent(open_kfid: str, candidates: list, preferred: str = "") -> str:
    """选择转接目标：优先熟人 > 最少接待量均衡"""
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    # 优先上次服务过的同事
    if preferred and preferred in candidates:
        return preferred

    # 均衡：选当前接待量最少的
    r = _get_redis()
    if r:
        best = candidates[0]
        best_load = _get_agent_load(r, open_kfid, best)
        for agent in candidates[1:]:
            load = _get_agent_load(r, open_kfid, agent)
            if load < best_load:
                best_load = load
                best = agent
        r.incr(f"{_LOAD_PREFIX}{open_kfid}:{best}")
        return best

    # 内存回退：简单轮转
    idx = transfer_next_index(open_kfid, len(candidates))
    return candidates[idx]


def _get_agent_load(r, open_kfid: str, agent: str) -> int:
    """获取某接待人员的当前接待量"""
    try:
        return int(r.get(f"{_LOAD_PREFIX}{open_kfid}:{agent}") or 0)
    except Exception:
        return 0


def transfer_complete(open_kfid: str, agent: str) -> None:
    """接待完成，减少该接待人员的负载计数"""
    r = _get_redis()
    if r:
        r.decr(f"{_LOAD_PREFIX}{open_kfid}:{agent}")


# ---- 用户→接待人绑定（优先上次服务过的同事） ----
_AGENT_PREFIX = "agent:"


def get_last_agent(external_userid: str) -> str:
    """获取用户上次转接的接待人员"""
    r = _get_redis()
    if r:
        return r.get(f"{_AGENT_PREFIX}{external_userid}") or ""
    return ""


def set_last_agent(external_userid: str, service_userid: str) -> None:
    """记录用户转接到的接待人员"""
    r = _get_redis()
    if r:
        r.set(f"{_AGENT_PREFIX}{external_userid}", service_userid, ex=86400 * 30)
    # 内存模式不记录（冷启动丢失可接受）


# ---- 用户护肤档案（长期记忆） ----
_PROFILE_PREFIX = "profile:"


def profile_get(open_kfid: str, external_userid: str) -> dict:
    """获取用户护肤档案"""
    r = _get_redis()
    if r:
        data = r.get(f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}")
        return json.loads(data) if data else {}
    return {}


def profile_update(open_kfid: str, external_userid: str, fields: dict) -> None:
    """更新用户护肤档案，合并新字段"""
    r = _get_redis()
    if r:
        current = profile_get(open_kfid, external_userid)
        current.update(fields)
        r.set(f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}", json.dumps(current, ensure_ascii=False), ex=86400 * 90)


# ---- 通用键值存储（短流程状态机） ----
_inmem_kv: dict[str, str] = {}


def kv_set(key: str, value: str, ttl: int = 600) -> None:
    """写入键值，带过期时间（秒）"""
    r = _get_redis()
    if r:
        r.set(key, value, ex=ttl)
        return
    _inmem_kv[key] = value


def kv_get(key: str) -> str:
    """读取键值，不存在返回空串"""
    r = _get_redis()
    if r:
        return r.get(key) or ""
    return _inmem_kv.get(key, "")


def kv_delete(key: str) -> None:
    """删除键"""
    r = _get_redis()
    if r:
        r.delete(key)
    _inmem_kv.pop(key, None)


# ---- 隐私授权状态 ----
_PRIVACY_PREFIX = "privacy:"


def privacy_notice_accepted(open_kfid: str, external_userid: str) -> bool:
    """检查用户是否已确认隐私声明"""
    r = _get_redis()
    if r:
        return r.get(f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}") == "1"
    return False


def privacy_notice_accept(open_kfid: str, external_userid: str) -> None:
    """记录用户已确认隐私声明"""
    r = _get_redis()
    if r:
        r.set(f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}", "1", ex=86400 * 365)
