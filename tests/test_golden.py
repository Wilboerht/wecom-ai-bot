"""prompt 质量黄金用例回归 — 调真实 LLM 端到端验证两个角色的行为红线

默认跳过（不消耗 API 额度）；需要回归时：
    RUN_GOLDEN=1 python -m pytest tests/test_golden.py -v

用例维护在 tests/golden_cases.json（客服顾问 12 条 + 护肤顾问 12 条）。
链路与生产一致：build_system_content（含知识库按需路由）+ 定界符包裹 + 输出侧禁语兜底。
"""

import json
import os
import pathlib

import pytest

from src.compliance import sanitize_reply
from src.core import build_profile_block, build_system_content, chat, wrap_user_text

_CASES_FILE = pathlib.Path(__file__).parent / "golden_cases.json"
_CASES = json.loads(_CASES_FILE.read_text(encoding="utf-8"))["cases"]

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_GOLDEN") != "1",
    reason="黄金用例需 RUN_GOLDEN=1 并配置真实 API key 才执行（会消耗额度）",
)


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_golden_case(case):
    # 可选多轮历史：模拟真实多轮对话（如模糊意图先征询、下一轮确认后出方案）
    history = [
        {"role": m["role"], "content": m["content"]} for m in case.get("history") or []
    ]
    # 可选档案预置：与生产链路同一函数/同一顺序（档案在注入防护声明之前）
    profile_text = build_profile_block(case.get("profile"))
    system_content = build_system_content(
        case["account"], case["user"], history, profile_text=profile_text
    )
    messages = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": wrap_user_text(case["user"])},
    ]
    reply, _ = sanitize_reply(chat(messages))

    for kw in case.get("must_contain") or []:
        assert kw in reply, f"[{case['id']}] 回复应包含 {kw!r}，实际: {reply[:300]}"
    for any_kws in case.get("must_contain_any") or []:
        assert any(kw in reply for kw in any_kws), (
            f"[{case['id']}] 回复应包含 {any_kws!r} 之一，实际: {reply[:300]}"
        )
    for kw in case.get("must_not_contain") or []:
        assert kw not in reply, f"[{case['id']}] 回复不应包含 {kw!r}，实际: {reply[:300]}"
