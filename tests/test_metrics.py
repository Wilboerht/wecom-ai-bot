"""src/metrics.py 轻量计数器测试

计数器是进程级全局状态，每个用例开始前先清零，避免用例间互相污染。
"""

import logging

import pytest

from src import metrics


@pytest.fixture(autouse=True)
def _clean_counters():
    metrics.snapshot(reset=True)
    yield
    metrics.snapshot(reset=True)


class TestCounters:
    def test_inc_and_snapshot(self):
        metrics.inc(metrics.KF_MSG_RECEIVED)
        metrics.inc(metrics.KF_MSG_RECEIVED)
        metrics.inc(metrics.AI_FAILURE, 3)

        data = metrics.snapshot()
        assert data[metrics.KF_MSG_RECEIVED] == 2
        assert data[metrics.AI_FAILURE] == 3

    def test_snapshot_returns_copy_not_reference(self):
        metrics.inc(metrics.AI_REPLY_OK)
        data = metrics.snapshot()
        data[metrics.AI_REPLY_OK] = 999
        assert metrics.snapshot()[metrics.AI_REPLY_OK] == 1

    def test_snapshot_reset_clears(self):
        metrics.inc(metrics.RATELIMIT_HIT)
        assert metrics.snapshot(reset=True)[metrics.RATELIMIT_HIT] == 1
        assert metrics.snapshot() == {}


class TestSummaryRates:
    def test_rates_computed(self):
        metrics.inc(metrics.KF_MSG_RECEIVED, 10)
        metrics.inc(metrics.AI_REPLY_OK, 8)
        metrics.inc(metrics.AI_FAILURE, 2)
        metrics.inc(metrics.TRANSFER_SUCCESS, 1)
        metrics.inc(metrics.RATELIMIT_HIT, 2)
        metrics.inc(metrics.ORDERFLOW_STARTED, 4)
        metrics.inc(metrics.ORDERFLOW_COMPLETED, 1)

        s = metrics.summary()
        assert s["counters"][metrics.KF_MSG_RECEIVED] == 10
        assert s["rates"]["ai_failure_rate"] == 0.2
        assert s["rates"]["transfer_rate"] == 0.1
        assert s["rates"]["ratelimit_rate"] == 0.2
        assert s["rates"]["orderflow_conversion"] == 0.25
        assert s["uptime_seconds"] >= 0

    def test_rates_none_when_denominator_zero(self):
        s = metrics.summary()
        assert s["counters"] == {}
        assert s["rates"]["ai_failure_rate"] is None
        assert s["rates"]["transfer_rate"] is None
        assert s["rates"]["orderflow_conversion"] is None


class TestFormatSummary:
    def test_format_no_counters(self):
        line = metrics.format_summary()
        assert "uptime=" in line and "无计数" in line
        assert "ai_failure_rate=-" in line

    def test_format_with_counters(self):
        metrics.inc(metrics.KF_MSG_RECEIVED, 3)
        metrics.inc(metrics.AI_REPLY_OK, 3)
        line = metrics.format_summary()
        assert "kf_msg_received=3" in line
        assert "ai_reply_ok=3" in line
        assert "ai_failure_rate=0.0" in line


class TestPeriodicLogger:
    def test_interval_zero_does_not_start(self, monkeypatch):
        monkeypatch.setattr(metrics, "_started", False)
        metrics.start_periodic_logger(0)
        assert metrics._started is False

    def test_interval_negative_does_not_start(self, monkeypatch):
        monkeypatch.setattr(metrics, "_started", False)
        metrics.start_periodic_logger(-5)
        assert metrics._started is False

    def test_starts_once_and_idempotent(self, monkeypatch):
        """首次调用启动线程并置 _started=True，重复调用不再新建线程"""
        monkeypatch.setattr(metrics, "_started", False)
        threads = []

        class FakeThread:
            def __init__(self, target, name, daemon=False):
                threads.append(self)

            def start(self):
                pass

        monkeypatch.setattr(metrics.threading, "Thread", FakeThread)
        metrics.start_periodic_logger(3600)
        metrics.start_periodic_logger(3600)
        assert len(threads) == 1
        assert metrics._started is True

    def test_loop_logs_summary_line(self, monkeypatch, caplog):
        """后台 loop 每轮打一行汇总：sleep 打桩为 0 并计数两轮后退出"""
        metrics.inc(metrics.KF_MSG_RECEIVED, 2)
        rounds = {"n": 0}

        def fake_sleep(seconds):
            rounds["n"] += 1
            if rounds["n"] >= 2:
                raise KeyboardInterrupt  # 不被 loop 内 except Exception 吞掉，退出循环

        monkeypatch.setattr(metrics, "_started", False)
        monkeypatch.setattr(metrics.time, "sleep", fake_sleep)

        captured = {}

        def fake_thread(target, name, daemon=False):
            captured["target"] = target

            class T:
                def start(self):
                    pass

            return T()

        monkeypatch.setattr(metrics.threading, "Thread", fake_thread)
        with caplog.at_level(logging.INFO, logger="src.metrics"):
            metrics.start_periodic_logger(3600)
            try:
                captured["target"]()
            except KeyboardInterrupt:
                pass
        assert any("业务指标:" in r.message and "kf_msg_received=2" in r.message
                   for r in caplog.records)
        metrics._started = False
