"""
Redis 持久化存储 — 会话、去重、限流、转接状态

若未配置 REDIS_URL，自动回退到内存模式（兼容单机部署）。
Redis 运行期故障时自动降级内存模式，并每 60 秒尝试重连恢复。
"""
import logging
import time
from ..config import config
from .storage import FallbackStorage, MemoryStorage

logger = logging.getLogger(__name__)

_redis = None
_redis_retry_at = 0.0  # Redis 故障后下次尝试重连的时间戳
REDIS_RETRY_INTERVAL = 60  # 故障后每 60 秒尝试重连一次


def _sanitize_redis_url(url: str) -> str:
    """连接串脱敏：只保留 host:port/db，去掉 userinfo，避免密码进日志"""
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        host = parts.hostname or "unknown"
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}{parts.path}"
    except Exception:
        return "<redacted>"


def _redis_down(e: Exception) -> None:
    """Redis 运行期故障：记 error 并降级内存模式，60 秒后自动重试"""
    global _redis, _redis_retry_at
    logger.error("Redis 操作失败，降级到内存模式: %s", e)
    _redis = False
    _redis_retry_at = time.time() + REDIS_RETRY_INTERVAL


def _get_redis():
    """获取 Redis 连接（懒加载 + 连接池）；故障降级内存模式并定时重连"""
    global _redis, _redis_retry_at
    if _redis:
        return _redis
    if _redis is False:
        # 故障冷却期：每 60 秒尝试一次重连，恢复后自动切回 Redis
        if time.time() < _redis_retry_at:
            return None
        _redis = None

    url = getattr(config, "REDIS_URL", "") or ""
    if not url:
        return None

    try:
        import redis
        client = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3, decode_responses=True)
        client.ping()
        _redis = client
        logger.info("Redis 连接成功: %s", _sanitize_redis_url(url))
        return _redis
    except Exception as e:
        logger.warning("Redis 连接失败，使用内存模式: %s", e)
        _redis = False
        _redis_retry_at = time.time() + REDIS_RETRY_INTERVAL
        return None


# ---- 会话存储 ----
_inmem_sessions: dict[str, list[dict]] = {}
_inmem_session_ttl: dict[str, float] = {}

SESSION_PREFIX = "sess:"


def session_get(user_id: str) -> list[dict]:
    """获取会话历史；单条损坏只跳过该条，不触发全局 Redis 降级"""
    return _storage.sessions.get(user_id)


def session_add(user_id: str, role: str, content: str, max_msgs: int = 20, ttl: int = 0) -> None:
    """追加消息到会话。媒体类内容（图片/语音）应只传元信息，不传原始二进制。"""
    ttl = ttl or config.SESSION_TTL
    if len(content) > 4096:
        logger.warning("session_add rejected: content too long (%d chars) — possible media data leak", len(content))
        content = content[:200] + "...[truncated]"
    _storage.sessions.add(user_id, role, content, max_msgs, ttl)


def _cleanup_sessions():
    """清理过期内存会话（兼容旧调用方）。"""
    _storage.sessions.cleanup()


# ---- 消息去重（原子 claim/release 语义） ----
_inmem_msgids: dict[str, float] = {}  # msgid -> 过期时间戳
MSGID_PREFIX = "kf:msgid:"
MSGID_TTL = 1800  # 30分钟


def msgid_claim(msgid: str) -> bool:
    """原子认领消息：True=首次认领（可处理），False=重复（应跳过）。"""
    return _storage.dedup.claim(msgid, MSGID_TTL)


def msgid_release(msgid: str) -> None:
    """回滚 msgid_claim（处理失败时调用，允许重试）"""
    _storage.dedup.release(msgid)


def _cleanup_msgids():
    """兼容旧调用方；内存实现由存储适配器惰性清理。"""
    return


def msgid_is_duplicate(msg_id: str, open_kfid: str) -> bool:
    """[兼容旧接口] 检查消息是否已处理，未处理则标记。
    新代码应使用 msgid_claim/msgid_release（处理失败可回滚）。"""
    return not msgid_claim(msg_id)


# ---- 速率限制 ----
_inmem_ratelimit: dict[str, list[float]] = {}
RATELIMIT_PREFIX = "rl:"


def ratelimit_check(user_id: str, send_time: int = 0, max_per_window: int = 30, window_secs: int = 60) -> bool:
    """检查是否超限，返回 True 表示被限制。"""
    return _storage.ratelimit.check(user_id, send_time, max_per_window, window_secs)


# ---- 轮流分配计数器 ----
_RR_PREFIX = "rr:"


def transfer_next_index(open_kfid: str, total: int) -> int:
    """获取下次转接应该分配给第几个人（轮轮流），Redis原子递增"""
    if total <= 1:
        return 0
    r = _get_redis()
    if r:
        try:
            idx = r.incr(f"{_RR_PREFIX}{open_kfid}") % total
            if idx < 0:
                idx = 0
            return idx
        except Exception as e:
            _redis_down(e)
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
        try:
            best = candidates[0]
            best_load = _get_agent_load(r, open_kfid, best)
            for agent in candidates[1:]:
                load = _get_agent_load(r, open_kfid, agent)
                if load < best_load:
                    best_load = load
                    best = agent
            r.incr(f"{_LOAD_PREFIX}{open_kfid}:{best}")
            # 计数键加 TTL，缓解长期不清理造成的永久堆积
            r.expire(f"{_LOAD_PREFIX}{open_kfid}:{best}", 86400)
            return best
        except Exception as e:
            _redis_down(e)

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
    """接待完成，减少该接待人员的负载计数。
    注意：当前无调用方（保留待用）；负载计数键靠 transfer_pick_agent 里的 TTL 自然过期"""
    r = _get_redis()
    if r:
        try:
            r.decr(f"{_LOAD_PREFIX}{open_kfid}:{agent}")
        except Exception as e:
            _redis_down(e)


# ---- 用户→接待人绑定（优先上次服务过的同事） ----
_AGENT_PREFIX = "agent:"


def get_last_agent(external_userid: str) -> str:
    """获取用户上次转接的接待人员"""
    if not _get_redis():
        _warn_memory_mode()
    return _storage.agents.get(external_userid)


def set_last_agent(external_userid: str, service_userid: str) -> None:
    """记录用户转接到的接待人员"""
    if not _get_redis():
        _warn_memory_mode()
    _storage.agents.set(external_userid, service_userid, LAST_AGENT_TTL)


# ---- 用户护肤档案（长期记忆） ----
_PROFILE_PREFIX = "profile:"
PROFILE_MAX_KEYS = 50  # 档案字段数量上限：超出后丢弃新增 key，防 prompt 成本膨胀
PROFILE_TTL = 86400 * 90
PRIVACY_TTL = 86400 * 365
LAST_AGENT_TTL = 86400 * 30

# 内存降级数据：值统一存 JSON 字符串 + 过期时间戳，语义与 Redis 对齐
_inmem_profiles: dict[str, tuple[str, float]] = {}
_inmem_privacy: dict[str, float] = {}
_inmem_last_agent: dict[str, tuple[str, float]] = {}

_memory_mode_warned = False


def _warn_memory_mode() -> None:
    """Redis 不可用时只告警一次，避免刷屏；内存模式重启会丢失数据"""
    global _memory_mode_warned
    if not _memory_mode_warned:
        _memory_mode_warned = True
        logger.warning(
            "Redis 未配置或不可用，使用内存存储；会话、档案、隐私授权和接待人绑定"
            "会在进程重启后丢失。多 worker 部署必须配置 Redis。"
        )


def _profile_key(open_kfid: str, external_userid: str) -> str:
    return f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}"


def _merge_profile_fields(current: dict, fields: dict) -> dict:
    """合并档案并执行字段数量上限；已有 key 可更新，新增 key 超出上限时丢弃"""
    new_keys = [k for k in fields if k not in current]
    if len(current) + len(new_keys) > PROFILE_MAX_KEYS:
        keep = max(0, PROFILE_MAX_KEYS - len(current))
        dropped = new_keys[keep:]
        logger.warning("档案字段数量超上限(%d)，丢弃新增字段: keys=%s", PROFILE_MAX_KEYS, dropped)
        kept = set(new_keys[:keep])
        fields = {k: v for k, v in fields.items() if k in current or k in kept}
    current.update(fields)
    return current


def profile_get(open_kfid: str, external_userid: str) -> dict:
    """获取用户护肤档案；JSON 损坏时由存储适配器清掉坏 key 并返回 {}。"""
    if not _get_redis():
        _warn_memory_mode()
    return _storage.profiles.get(_profile_key(open_kfid, external_userid))


def profile_update(open_kfid: str, external_userid: str, fields: dict) -> None:
    """更新用户护肤档案，合并新字段；key 总数超 PROFILE_MAX_KEYS 时保留已有 key 的更新。"""
    if not _get_redis():
        _warn_memory_mode()
    key = _profile_key(open_kfid, external_userid)
    current = _merge_profile_fields(_storage.profiles.get(key), fields)
    _storage.profiles.set(key, current, PROFILE_TTL)


def profile_delete_keys(open_kfid: str, external_userid: str, keys: list) -> None:
    """删除用户护肤档案中的指定字段。"""
    if not keys:
        return
    if not _get_redis():
        _warn_memory_mode()
    key = _profile_key(open_kfid, external_userid)
    current = _storage.profiles.get(key)
    if not current:
        return
    for k in keys:
        current.pop(k, None)
    if current:
        _storage.profiles.set(key, current, PROFILE_TTL)
    else:
        _storage.profiles.delete(key)


def profile_replace_report_keys(open_kfid: str, external_userid: str, delete_keys: list, fields: dict) -> None:
    """测肤报告字段原子替换：一次 read→删除旧报告字段→并入新字段→单次写入。"""
    if not _get_redis():
        _warn_memory_mode()
    key = _profile_key(open_kfid, external_userid)
    current = _storage.profiles.get(key)
    for k in delete_keys:
        current.pop(k, None)
    current.update(fields)
    _storage.profiles.set(key, current, PROFILE_TTL)


# ---- 通用键值存储（短流程状态机） ----
_inmem_kv: dict[str, tuple[str, float]] = {}  # key -> (value, 过期时间戳)


def kv_set(key: str, value: str, ttl: int = 600) -> None:
    """写入键值，带过期时间（秒）。"""
    _storage.kv.set(key, value, ttl)


def kv_set_nx(key: str, value: str, ttl: int = 600) -> bool:
    """键不存在才写入（Redis SET NX EX），返回是否占用成功。"""
    return _storage.kv.set_nx(key, value, ttl)


def kv_get(key: str) -> str:
    """读取键值，不存在或已过期返回空串"""
    return _storage.kv.get(key)


def kv_delete(key: str) -> None:
    """删除键"""
    _storage.kv.delete(key)


# ---- 隐私授权状态 ----
_PRIVACY_PREFIX = "privacy:"


def privacy_notice_accepted(open_kfid: str, external_userid: str) -> bool:
    """检查用户是否已确认隐私声明"""
    if not _get_redis():
        _warn_memory_mode()
    key = f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}"
    return _storage.privacy.accepted(key)


def privacy_notice_accept(open_kfid: str, external_userid: str) -> None:
    """记录用户已确认隐私声明"""
    if not _get_redis():
        _warn_memory_mode()
    key = f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}"
    _storage.privacy.accept(key, PRIVACY_TTL)


# ---- PII 导出/删除（个人信息保护法 查阅权/删除权） ----
# 按 (open_kfid, external_userid) 维度键控的用户数据 key：
#   sess:{kfid}:{euid}                 会话历史 LIST（SESSION_TTL，默认 30 分钟）
#   profile:{kfid}:{euid}              用户护肤档案 STRING（90 天，含过敏史/孕期等敏感字段）
#   privacy:{kfid}:{euid}              隐私告知确认标记 STRING（365 天）
#   orderflow:{kfid}:{euid}            订单查询流程状态 kv（600s）
#   kf:transferred:{kfid}:{euid}       转人工跨批次标记 kv（300s）
#   welcome-fallback:{kfid}:{euid}:*   欢迎语去重 kv（7 天，按报告 scene_param）
#   review-due:{kfid}:{euid}:*         复评指令触达标记 kv（90 天，按随访日期）
#   welcome-sent:{kfid}:{euid}         通用欢迎语已发标记 kv（365 天，与报告欢迎语去重共用）
#   agent:{euid}                       用户→上次接待人绑定 STRING（30 天，不带 kfid）
#   rl:{euid}                          限流时间戳 ZSET（约 120s，不带 kfid）
# 明确不覆盖：kf:msgid:{msgid} 去重键按消息而非按用户存储，无法按用户枚举，1800s 自然过期。
# Redis 不可用时上述用户数据同样写入进程内存，export/delete 覆盖内存侧数据。

_WELCOME_FALLBACK_PREFIX = "welcome-fallback:"
_REVIEW_DUE_PREFIX = "review-due:"


def _user_kv_keys(open_kfid: str, external_userid: str) -> list[str]:
    """该用户键控的 kv key 清单：固定三个 + welcome-fallback / review-due 前缀扫描"""
    fixed = [
        f"orderflow:{open_kfid}:{external_userid}",
        f"kf:transferred:{open_kfid}:{external_userid}",
        f"welcome-sent:{open_kfid}:{external_userid}",
    ]
    pattern_prefixes = [
        f"{_WELCOME_FALLBACK_PREFIX}{open_kfid}:{external_userid}:",
        f"{_REVIEW_DUE_PREFIX}{open_kfid}:{external_userid}:",
    ]
    r = _get_redis()
    if r:
        try:
            scanned = []
            for prefix in pattern_prefixes:
                scanned.extend(r.scan_iter(match=f"{prefix}*", count=100))
            return fixed + scanned
        except Exception as e:
            _redis_down(e)
    return fixed + [
        k for k in _inmem_kv if any(k.startswith(prefix) for prefix in pattern_prefixes)
    ]


def _ratelimit_export(external_userid: str) -> list[float] | None:
    """限流窗口内的时间戳列表（无数据返回 None）"""
    r = _get_redis()
    if r:
        try:
            items = r.zrange(f"{RATELIMIT_PREFIX}{external_userid}", 0, -1, withscores=True)
            scores = [score for _, score in items]
            return scores or None
        except Exception as e:
            _redis_down(e)
    timestamps = _inmem_ratelimit.get(external_userid)
    return list(timestamps) if timestamps else None


def export_user_data(open_kfid: str, external_userid: str) -> dict:
    """导出该用户的全部数据（PIPL 查阅权）。各部分无数据时为 null。
    msgid 去重键按消息存储无法按用户枚举，不包含（1800s 自然过期）。"""
    user_id = f"{open_kfid}:{external_userid}"
    kv: dict[str, str] = {}
    for key in _user_kv_keys(open_kfid, external_userid):
        value = kv_get(key)
        if value:
            kv[key] = value
    return {
        "open_kfid": open_kfid,
        "external_userid": external_userid,
        "session": session_get(user_id) or None,
        "profile": profile_get(open_kfid, external_userid) or None,
        "privacy_notice_accepted": privacy_notice_accepted(open_kfid, external_userid),
        "kv": kv or None,
        "last_agent": get_last_agent(external_userid) or None,
        "ratelimit": _ratelimit_export(external_userid),
    }


def delete_user_data(open_kfid: str, external_userid: str) -> list[str]:
    """删除该用户的全部数据（PIPL 删除权），返回实际删除的 key 清单（类别即 key 前缀）。
    msgid 去重键按消息存储无法按用户枚举，不删除（1800s 自然过期）。"""
    user_id = f"{open_kfid}:{external_userid}"
    keys = [
        f"{SESSION_PREFIX}{user_id}",
        f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}",
        f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}",
        f"{_AGENT_PREFIX}{external_userid}",
        f"{RATELIMIT_PREFIX}{external_userid}",
        *_user_kv_keys(open_kfid, external_userid),
    ]
    deleted: list[str] = []
    r = _get_redis()
    if r:
        try:
            for key in keys:
                if r.delete(key):
                    deleted.append(key)
            return deleted
        except Exception as e:
            _redis_down(e)
    # 内存模式：会话 / 档案 / 隐私授权 / 接待人绑定 / 限流 / kv
    if _inmem_sessions.pop(user_id, None) is not None:
        deleted.append(f"{SESSION_PREFIX}{user_id}")
    _inmem_session_ttl.pop(user_id, None)
    if _inmem_profiles.pop(f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}", None) is not None:
        deleted.append(f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}")
    if _inmem_privacy.pop(f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}", None) is not None:
        deleted.append(f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}")
    if _inmem_last_agent.pop(external_userid, None) is not None:
        deleted.append(f"{_AGENT_PREFIX}{external_userid}")
    if _inmem_ratelimit.pop(external_userid, None) is not None:
        deleted.append(f"{RATELIMIT_PREFIX}{external_userid}")
    for key in keys:
        if _inmem_kv.pop(key, None) is not None:
            deleted.append(key)
    return deleted

# 存储端口：Redis 优先、失败降级内存；模块级 dict 继续作为兼容别名供测试/运维检查
_memory = MemoryStorage(
    sessions=_inmem_sessions,
    session_ttl=_inmem_session_ttl,
    profiles=_inmem_profiles,
    privacy=_inmem_privacy,
    agents=_inmem_last_agent,
    kv=_inmem_kv,
    msgids=_inmem_msgids,
    ratelimit=_inmem_ratelimit,
)
_storage = FallbackStorage(
    # 动态解析模块全局，测试/运行期 monkeypatch _get_redis 仍然生效
    redis_getter=lambda: _get_redis(),
    memory=_memory,
    on_down=lambda e: _redis_down(e),
)
