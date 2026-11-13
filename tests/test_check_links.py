"""scripts/check_links.py 的本地检查逻辑。"""

from datetime import datetime, timezone
from pathlib import Path

from scripts.check_links import apptime_age_days, check_url_format, main


def test_apptime_age_days():
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    url = "https://www.xiaohongshu.hk/goods-detail/1?apptime=1786598828"
    assert apptime_age_days(url, now) == 91


def test_apptime_missing_returns_none():
    assert apptime_age_days("https://example.com/a") is None


def test_url_format():
    assert check_url_format("https://example.com/a") is None
    assert check_url_format("javascript:alert(1)") is not None
    assert check_url_format("not-a-url") is not None


def test_main_reports_stale_and_strict(tmp_path: Path, capsys):
    path = tmp_path / "links.json"
    path.write_text(
        '{"xhs": "https://www.xiaohongshu.hk/goods-detail/1?apptime=1"}',
        encoding="utf-8",
    )
    assert main(["--file", str(path), "--strict", "--max-age-days", "1"]) == 1
    assert "apptime" in capsys.readouterr().out
