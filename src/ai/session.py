import time
from collections import defaultdict
from ..config import config


class SessionManager:
    """内存会话管理器：按用户 ID 维护对话上下文"""

    def __init__(self):
        self._sessions: dict[str, list[dict]] = defaultdict(list)
        self._last_active: dict[str, float] = {}

    def get_context(self, user_id: str) -> list[dict]:
        """获取用户对话历史（已自动清理过期会话）"""
        self._maybe_expire(user_id)
        return list(self._sessions[user_id])

    def add_message(self, user_id: str, role: str, content: str) -> None:
        """追加一条消息到会话历史"""
        self._sessions[user_id].append({"role": role, "content": content})
        self._last_active[user_id] = time.time()
        self._trim(user_id)

    def reset(self, user_id: str) -> None:
        """重置用户会话"""
        self._sessions.pop(user_id, None)
        self._last_active.pop(user_id, None)

    def _maybe_expire(self, user_id: str) -> None:
        """检查并清理过期会话"""
        last = self._last_active.get(user_id)
        if last and (time.time() - last) > config.SESSION_TTL:
            self.reset(user_id)

    def _trim(self, user_id: str) -> None:
        """限制上下文长度：最多保留最近 10 轮对话"""
        max_messages = 20
        if len(self._sessions[user_id]) > max_messages:
            self._sessions[user_id] = self._sessions[user_id][-max_messages:]


session_manager = SessionManager()
