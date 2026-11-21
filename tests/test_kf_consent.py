import hashlib
import hmac
import json
import time

from src import kf_consent
from src.config import config


class _Response:
    status_code = 200


def test_link_identity_posts_signed_request(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_KEY", "kf-key")
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_SECRET", "s" * 32)
    monkeypatch.setattr(config, "KF_MAIN_API_BASE", "http://main.test")
    monkeypatch.setattr(
        kf_consent.httpx,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs)) or _Response(),
    )

    kf_consent.link_identity("wk1", "wm1", "user-1")

    assert calls
    url, kwargs = calls[0]
    assert url == "http://main.test/api/internal/kf/identity-link"
    assert kwargs["headers"]["X-Internal-API-Key"] == "kf-key"
    payload = json.loads(kwargs["content"])
    assert payload["userId"] == "user-1"


def test_record_event_posts_to_main(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_KEY", "kf-key")
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_SECRET", "s" * 32)
    monkeypatch.setattr(config, "KF_MAIN_API_BASE", "http://main.test")
    monkeypatch.setattr(kf_consent, "get_legal_content_hash", lambda: "privacy-hash")
    monkeypatch.setattr(
        kf_consent.httpx,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs)) or _Response(),
    )

    kf_consent.record_event("wk1", "wm1", "GRANTED", "2026-11-19")

    assert calls
    url, kwargs = calls[0]
    assert url == "http://main.test/api/internal/kf/consent-event"
    payload = json.loads(kwargs["content"])
    assert payload["action"] == "GRANTED"


def test_record_event_omits_unavailable_content_hash(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_KEY", "kf-key")
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_SECRET", "s" * 32)
    monkeypatch.setattr(config, "KF_MAIN_API_BASE", "http://main.test")
    monkeypatch.setattr(kf_consent, "get_legal_content_hash", lambda: None)
    monkeypatch.setattr(
        kf_consent.httpx,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs)) or _Response(),
    )

    kf_consent.record_event("wk1", "wm1", "GRANTED", "2026-11-19")

    payload = json.loads(calls[0][1]["content"])
    assert "contentHash" not in payload


def test_no_key_skips_request(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_KEY", "")
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_SECRET", "")
    monkeypatch.setattr(
        kf_consent.httpx,
        "post",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    kf_consent.record_event("wk1", "wm1", "GRANTED", "2026-11-19")
    assert calls == []


def test_failed_event_is_queued_and_flushed(monkeypatch):
    monkeypatch.setattr(kf_consent.store, "_get_redis", lambda: None)
    monkeypatch.setattr(kf_consent, "_post", lambda path, payload: False)
    monkeypatch.setattr(kf_consent, "get_legal_content_hash", lambda: None)
    kf_consent._inmem_outbox.clear()

    kf_consent.record_event("wk1", "wm1", "GRANTED", "2026-11-19")
    assert len(kf_consent._inmem_outbox) == 1

    monkeypatch.setattr(kf_consent, "_post", lambda path, payload: True)
    assert kf_consent.flush_outbox() == 1
    assert kf_consent._inmem_outbox == []


def test_verify_main_request_accepts_valid_signature(monkeypatch):
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_KEY", "kf-key")
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_SECRET", "s" * 32)
    path = "/api/internal/pii/delete"
    body = json.dumps({"openKfid": "wk1", "externalUserId": "wm1"}).encode()
    timestamp = str(int(time.time()))
    nonce = "nonce"
    body_hash = hashlib.sha256(body).hexdigest()
    signature = hmac.new(
        ("s" * 32).encode(),
        f"POST|{path}||{timestamp}|{nonce}|{body_hash}".encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "x-internal-api-key": "kf-key",
        "x-internal-api-timestamp": timestamp,
        "x-internal-api-nonce": nonce,
        "x-internal-api-signature": signature,
    }
    assert kf_consent.verify_main_request(headers, path, body) is True


def test_verify_main_request_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_KEY", "kf-key")
    monkeypatch.setattr(config, "KF_MAIN_INTERNAL_SECRET", "s" * 32)
    body = b"{}"
    headers = {
        "x-internal-api-key": "kf-key",
        "x-internal-api-timestamp": str(int(time.time())),
        "x-internal-api-nonce": "n",
        "x-internal-api-signature": "bad",
    }
    assert kf_consent.verify_main_request(headers, "/api/internal/pii/delete", body) is False
