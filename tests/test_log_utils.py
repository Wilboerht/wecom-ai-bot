"""日志脱敏工具测试。"""

from src.log_utils import mask_id, sanitize_log_value


class TestMaskId:
    def test_masks_external_userid(self):
        assert mask_id("wmABCdef1234567890") == "wmABCdef***"

    def test_short_id_never_returns_full_value(self):
        assert mask_id("abc") == "ab***"

    def test_empty(self):
        assert mask_id("") == "-"

    def test_invalid_chars_are_not_logged(self):
        assert mask_id("bad\nid") == "<invalid-id>"


class TestSanitizeLogValue:
    def test_removes_control_chars_and_newlines(self):
        assert sanitize_log_value("a\nb\tc\x00d") == "a?b?c?d"

    def test_truncates(self):
        assert sanitize_log_value("x" * 300, 20) == "x" * 20

    def test_none(self):
        assert sanitize_log_value(None) == "-"
