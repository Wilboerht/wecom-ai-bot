"""订单查询引导流程 — 解析辅助函数测试"""

from src.core import (
    _extract_order_no,
    _split_channel_order,
    _order_triggered,
    _is_policy_question,
    _order_transfer_candidates,
)


class TestTransferCandidates:
    def test_last_agent_first(self):
        assert _order_transfer_candidates(["A", "B"], "B") == ["B", "A"]

    def test_last_agent_not_in_list(self):
        assert _order_transfer_candidates(["A", "B"], "C") == ["A", "B"]

    def test_no_last_agent(self):
        assert _order_transfer_candidates(["A", "B"], "") == ["A", "B"]

    def test_single_candidate(self):
        assert _order_transfer_candidates(["A"], "A") == ["A"]
        assert _order_transfer_candidates(["A"], "") == ["A"]

    def test_empty(self):
        assert _order_transfer_candidates([], "A") == []


class TestPolicyQuestion:
    def test_policy_question_skips_transfer(self):
        assert _is_policy_question("你们的退换货政策是什么")
        assert _is_policy_question("退货运费谁承担")
        assert _is_policy_question("怎么退货")
        assert _is_policy_question("产品会过敏吗")
        assert _is_policy_question("能换货吗")

    def test_action_requests_still_transfer(self):
        assert not _is_policy_question("我要退货")
        assert not _is_policy_question("给我退款")
        assert not _is_policy_question("我过敏了，脸都红了")
        assert not _is_policy_question("我要投诉")

    def test_irrelevant_text(self):
        assert not _is_policy_question("你们有哪些产品")
        assert not _is_policy_question("你好")


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
