"""内存模式下的档案/隐私/接待人绑定与 PII 覆盖。"""

from src.ai import store


class TestMemoryProfilePrivacyAgent:
    def test_profile_roundtrip_and_delete(self, monkeypatch):
        monkeypatch.setattr(store, "_get_redis", lambda: None)
        monkeypatch.setattr(store, "_memory_mode_warned", True)
        kfid, euid = "wkMem", "uMemProfile"
        try:
            store.profile_update(kfid, euid, {"肤质": "干性"})
            store.profile_update(kfid, euid, {"诉求": "泛红"})
            assert store.profile_get(kfid, euid) == {"肤质": "干性", "诉求": "泛红"}

            store.profile_delete_keys(kfid, euid, ["肤质"])
            assert store.profile_get(kfid, euid) == {"诉求": "泛红"}

            store.profile_replace_report_keys(kfid, euid, ["诉求"], {"肤质": "油性"})
            assert store.profile_get(kfid, euid) == {"肤质": "油性"}
        finally:
            store._inmem_profiles.pop(f"profile:{kfid}:{euid}", None)

    def test_privacy_roundtrip(self, monkeypatch):
        monkeypatch.setattr(store, "_get_redis", lambda: None)
        monkeypatch.setattr(store, "_memory_mode_warned", True)
        kfid, euid = "wkMem", "uMemPrivacy"
        try:
            assert store.privacy_notice_accepted(kfid, euid) is False
            store.privacy_notice_accept(kfid, euid)
            assert store.privacy_notice_accepted(kfid, euid) is True
        finally:
            store._inmem_privacy.pop(f"privacy:{kfid}:{euid}", None)

    def test_last_agent_roundtrip(self, monkeypatch):
        monkeypatch.setattr(store, "_get_redis", lambda: None)
        monkeypatch.setattr(store, "_memory_mode_warned", True)
        euid = "uMemAgent"
        try:
            assert store.get_last_agent(euid) == ""
            store.set_last_agent(euid, "agent01")
            assert store.get_last_agent(euid) == "agent01"
        finally:
            store._inmem_last_agent.pop(euid, None)

    def test_pii_export_and_delete_cover_memory_data(self, monkeypatch):
        monkeypatch.setattr(store, "_get_redis", lambda: None)
        monkeypatch.setattr(store, "_memory_mode_warned", True)
        kfid, euid = "wkMem", "uMemPii"
        uid = f"{kfid}:{euid}"
        try:
            store.session_add(uid, "user", "hi")
            store.profile_update(kfid, euid, {"肤质": "干性"})
            store.privacy_notice_accept(kfid, euid)
            store.set_last_agent(euid, "agent01")
            data = store.export_user_data(kfid, euid)
            assert data["session"][0]["content"] == "hi"
            assert data["profile"] == {"肤质": "干性"}
            assert data["privacy_notice_accepted"] is True
            assert data["last_agent"] == "agent01"

            deleted = store.delete_user_data(kfid, euid)
            assert f"profile:{kfid}:{euid}" in deleted
            assert f"privacy:{kfid}:{euid}" in deleted
            assert f"agent:{euid}" in deleted
            again = store.export_user_data(kfid, euid)
            assert again["profile"] is None
            assert again["privacy_notice_accepted"] is False
            assert again["last_agent"] is None
        finally:
            store._inmem_sessions.pop(uid, None)
            store._inmem_session_ttl.pop(uid, None)
            store._inmem_profiles.pop(f"profile:{kfid}:{euid}", None)
            store._inmem_privacy.pop(f"privacy:{kfid}:{euid}", None)
            store._inmem_last_agent.pop(euid, None)
