"""内存存储适配器（单进程/降级）。"""
import json
import time


class MemoryStorage:
    def __init__(
        self,
        *,
        sessions: dict[str, list[dict]],
        session_ttl: dict[str, float],
        profiles: dict[str, tuple[str, float]],
        privacy: dict[str, float],
        agents: dict[str, tuple[str, float]],
        kv: dict[str, tuple[str, float]],
        msgids: dict[str, float],
        ratelimit: dict[str, list[float]],
    ):
        self.sessions = _MemorySessions(sessions, session_ttl)
        self.profiles = _MemoryProfiles(profiles)
        self.privacy = _MemoryPrivacy(privacy)
        self.agents = _MemoryAgents(agents)
        self.kv = _MemoryKv(kv)
        self.dedup = _MemoryDedup(msgids)
        self.ratelimit = _MemoryRateLimit(ratelimit)

    def cleanup_expired(self, table: dict) -> None:
        now = time.time()
        for key in list(table.keys()):
            item = table[key]
            expire_at = item[1] if isinstance(item, tuple) else item
            if expire_at <= now:
                table.pop(key, None)


class _MemorySessions:
    def __init__(self, sessions: dict[str, list[dict]], ttl: dict[str, float]):
        self._sessions = sessions
        self._ttl = ttl

    def cleanup(self) -> None:
        now = time.time()
        for uid in list(self._ttl.keys()):
            if self._ttl[uid] <= now:
                self._sessions.pop(uid, None)
                self._ttl.pop(uid, None)

    def get(self, user_id: str) -> list[dict]:
        self.cleanup()
        return [dict(m) for m in self._sessions.get(user_id, []) if isinstance(m, dict)]

    def add(self, user_id: str, role: str, content: str, max_msgs: int, ttl: int) -> None:
        if user_id not in self._sessions:
            self._sessions[user_id] = []
        self._sessions[user_id].append({"role": role, "content": content})
        self._ttl[user_id] = time.time() + ttl
        if len(self._sessions[user_id]) > max_msgs:
            self._sessions[user_id] = self._sessions[user_id][-max_msgs:]


class _MemoryProfiles:
    def __init__(self, data: dict[str, tuple[str, float]]):
        self._data = data

    def _cleanup(self) -> None:
        now = time.time()
        for key in list(self._data.keys()):
            if self._data[key][1] <= now:
                self._data.pop(key, None)

    def get(self, key: str) -> dict:
        self._cleanup()
        item = self._data.get(key)
        if not item:
            return {}
        try:
            parsed = json.loads(item[0])
        except json.JSONDecodeError:
            self._data.pop(key, None)
            return {}
        if not isinstance(parsed, dict):
            self._data.pop(key, None)
            return {}
        return parsed

    def set(self, key: str, data: dict, ttl: int) -> None:
        self._data[key] = (json.dumps(data, ensure_ascii=False), time.time() + ttl)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _MemoryKv:
    def __init__(self, data: dict[str, tuple[str, float]]):
        self._data = data

    def get(self, key: str) -> str:
        item = self._data.get(key)
        if not item:
            return ""
        value, expire_at = item
        if expire_at <= time.time():
            self._data.pop(key, None)
            return ""
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._data[key] = (value, time.time() + ttl)

    def set_nx(self, key: str, value: str, ttl: int) -> bool:
        item = self._data.get(key)
        if item and item[1] > time.time():
            return False
        self._data[key] = (value, time.time() + ttl)
        return True

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _MemoryPrivacy:
    def __init__(self, data: dict[str, float]):
        self._data = data

    def accepted(self, key: str) -> bool:
        return self._data.get(key, 0) > time.time()

    def accept(self, key: str, ttl: int) -> None:
        self._data[key] = time.time() + ttl


class _MemoryAgents:
    def __init__(self, data: dict[str, tuple[str, float]]):
        self._data = data

    def get(self, external_userid: str) -> str:
        item = self._data.get(external_userid)
        if not item:
            return ""
        if item[1] <= time.time():
            self._data.pop(external_userid, None)
            return ""
        return item[0]

    def set(self, external_userid: str, agent: str, ttl: int) -> None:
        self._data[external_userid] = (agent, time.time() + ttl)


class _MemoryDedup:
    def __init__(self, data: dict[str, float]):
        self._data = data

    def _cleanup(self) -> None:
        now = time.time()
        for msgid in list(self._data.keys()):
            if self._data[msgid] <= now:
                self._data.pop(msgid, None)

    def claim(self, msgid: str, ttl: int) -> bool:
        self._cleanup()
        if msgid in self._data:
            return False
        self._data[msgid] = time.time() + ttl
        return True

    def release(self, msgid: str) -> None:
        self._data.pop(msgid, None)


class _MemoryRateLimit:
    def __init__(self, data: dict[str, list[float]]):
        self._data = data

    def check(self, user_id: str, send_time: int, max_per_window: int, window_secs: int) -> bool:
        now = time.time()
        if send_time and now - send_time > window_secs:
            return False
        timestamps = [t for t in self._data.get(user_id, []) if now - t < window_secs]
        self._data[user_id] = timestamps
        if len(timestamps) >= max_per_window:
            return True
        timestamps.append(send_time or now)
        return False
