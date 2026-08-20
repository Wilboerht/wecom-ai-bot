"""外部 prompt 文件 — mtime 热加载 / 缺失回退 / system prompt 缓存联动"""

import logging
import os

import pytest

from src import core
from src.core import get_system_prompt, load_prompt_file


@pytest.fixture(autouse=True)
def _clear_prompt_caches():
    """模块级缓存跨用例清空，避免 tmp_path 文件名复用造成误判"""
    core._prompt_file_cache.clear()
    core._account_prompts.clear()
    yield
    core._prompt_file_cache.clear()
    core._account_prompts.clear()


def _write(fp, content: str, mtime: float = None) -> None:
    fp.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(fp, (mtime, mtime))


class TestLoadPromptFile:
    def test_reads_file(self, tmp_path):
        fp = tmp_path / "p.txt"
        _write(fp, "你是客服\n", mtime=1000000.0)
        assert load_prompt_file(str(fp)) == "你是客服"

    def test_mtime_change_triggers_reload(self, tmp_path):
        fp = tmp_path / "p.txt"
        _write(fp, "版本一", mtime=1000000.0)
        assert load_prompt_file(str(fp)) == "版本一"

        _write(fp, "版本二", mtime=1000100.0)
        assert load_prompt_file(str(fp)) == "版本二"

    def test_same_mtime_hits_cache(self, tmp_path):
        fp = tmp_path / "p.txt"
        _write(fp, "内容", mtime=1000000.0)
        assert load_prompt_file(str(fp)) is load_prompt_file(str(fp))

    def test_missing_file_returns_empty_and_warns_once(self, tmp_path, caplog):
        path = str(tmp_path / "nonexistent.txt")
        with caplog.at_level(logging.WARNING, logger="src.core"):
            assert load_prompt_file(path) == ""
            assert load_prompt_file(path) == ""
        warnings = [r for r in caplog.records if "prompt 文件未找到" in r.getMessage()]
        assert len(warnings) == 1


class TestGetSystemPromptWithFile:
    def test_uses_prompt_file(self, tmp_path, monkeypatch):
        fp = tmp_path / "role.txt"
        _write(fp, "外部提示词", mtime=1000000.0)
        monkeypatch.setattr(
            "src.core.config.get_account",
            lambda kfid: {"system_prompt_file": str(fp), "knowledge_file": ""},
        )
        assert get_system_prompt("kf-pf-1") == "外部提示词"

    def test_file_takes_precedence_over_inline(self, tmp_path, monkeypatch):
        """system_prompt_file 存在且非空时优先于内联 system_prompt"""
        fp = tmp_path / "role.txt"
        _write(fp, "文件版", mtime=1000000.0)
        monkeypatch.setattr(
            "src.core.config.get_account",
            lambda kfid: {
                "system_prompt_file": str(fp),
                "system_prompt": "内联版",
                "knowledge_file": "",
            },
        )
        assert get_system_prompt("kf-pf-2") == "文件版"

    def test_missing_file_falls_back_to_inline(self, tmp_path, monkeypatch):
        """prompt 文件缺失时回退内联 system_prompt（不阻断服务）"""
        monkeypatch.setattr(
            "src.core.config.get_account",
            lambda kfid: {
                "system_prompt_file": str(tmp_path / "gone.txt"),
                "system_prompt": "内联版",
                "knowledge_file": "",
            },
        )
        assert get_system_prompt("kf-pf-3") == "内联版"

    def test_prompt_file_change_rebuilds_prompt(self, tmp_path, monkeypatch):
        """prompt 文件热加载变化时重建拼接结果（缓存不只盯知识库）"""
        fp = tmp_path / "role.txt"
        kfp = tmp_path / "kb.txt"
        _write(fp, "提示词A", mtime=1000000.0)
        _write(kfp, "Q: 价格\nA: 100元\n", mtime=2000000.0)
        monkeypatch.setattr(
            "src.core.config.get_account",
            lambda kfid: {"system_prompt_file": str(fp), "knowledge_file": str(kfp)},
        )

        first = get_system_prompt("kf-pf-4")
        assert "提示词A" in first and "100元" in first

        _write(fp, "提示词B", mtime=1000100.0)
        second = get_system_prompt("kf-pf-4")
        assert "提示词B" in second and "提示词A" not in second

    def test_unchanged_hits_cache(self, tmp_path, monkeypatch):
        fp = tmp_path / "role.txt"
        _write(fp, "提示词", mtime=1000000.0)
        monkeypatch.setattr(
            "src.core.config.get_account",
            lambda kfid: {"system_prompt_file": str(fp), "knowledge_file": ""},
        )
        assert get_system_prompt("kf-pf-5") is get_system_prompt("kf-pf-5")


class TestRealAccountsSharePromptFile:
    """kf_accounts.json 两个品牌客服账号应共用同一份 prompt 文件（防漂移）"""

    def test_brand_accounts_share_prompt_file(self):
        from src.config import config

        a1 = config.get_account("wkBpRSVwAA_pArJA-t5WEOLTlS1PB1FQ")
        a2 = config.get_account("kfcb074403c752c0645")
        assert a1.get("system_prompt_file") == a2.get("system_prompt_file") != ""
        assert os.path.exists(a1["system_prompt_file"])
        # 漂移防线：共用文件意味着两账号 prompt 必然一致
        assert get_system_prompt("wkBpRSVwAA_pArJA-t5WEOLTlS1PB1FQ") == get_system_prompt(
            "kfcb074403c752c0645"
        )
