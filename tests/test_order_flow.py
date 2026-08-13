"""订单查询引导流程 — 解析辅助函数测试"""

from src.core import _extract_order_no, _split_channel_order, _order_triggered


class TestOrderFlow:
    def test_trigger_detect(self):
        assert _order_triggered("帮我查询我的订单")
        assert _order_triggered("查物流")
        assert _order_triggered("物流到哪了")
        assert _order_triggered("快递单号是多少")
        assert _order_triggered("订单进度")
        assert _order_triggered("我的订单在哪看")
        assert _order_triggered("怎么查快递")
        assert not _order_triggered("面霜多少钱")
        assert not _order_triggered("你好")
        assert not _order_triggered("帮我看看这个成分")

    def test_extract_order_no_prefix(self):
        assert _extract_order_no("订单号1234567890") == "1234567890"
        assert _extract_order_no("单号：ABC12345") == "ABC12345"
        assert _extract_order_no("订单号 abc-123456") == "abc-123456"

    def test_extract_order_no_bare_digits(self):
        assert _extract_order_no("1234567890") == "1234567890"

    def test_extract_order_no_too_short(self):
        assert _extract_order_no("123456") == ""

    def test_extract_order_no_none(self):
        assert _extract_order_no("天猫买的") == ""

    def test_split_channel_order(self):
        ch, no = _split_channel_order("天猫买的，订单号1234567890")
        assert ch == "天猫买的"
        assert no == "1234567890"

    def test_split_channel_only(self):
        ch, no = _split_channel_order("小红书")
        assert ch == "小红书"
        assert no == ""

    def test_split_bare_digits(self):
        ch, no = _split_channel_order("抖音 1234567890")
        assert ch == "抖音"
        assert no == "1234567890"
