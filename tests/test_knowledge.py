"""知识库加载 — mtime 热加载 / 空行终止续行 / system prompt 缓存联动"""

import os

from src.ai.knowledge import load_knowledge
from src.core import get_system_prompt


def _write(fp, content: str, mtime: float = None) -> None:
    fp.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(fp, (mtime, mtime))


class TestHotReload:
    def test_mtime_change_triggers_reload(self, tmp_path):
        """文件 mtime 变化后重新加载，无需重启进程"""
        fp = tmp_path / "kb1.txt"
        _write(fp, "Q: 价格\nA: 100元\n", mtime=1000000.0)
        first = load_knowledge(str(fp))
        assert "100元" in first

        _write(fp, "Q: 价格\nA: 200元\n", mtime=1000100.0)
        second = load_knowledge(str(fp))
        assert "200元" in second
        assert "100元" not in second

    def test_same_mtime_hits_cache(self, tmp_path):
        """mtime 未变时命中缓存（返回同一对象，不重复解析）"""
        fp = tmp_path / "kb2.txt"
        _write(fp, "Q: 你好\nA: 您好\n")
        assert load_knowledge(str(fp)) is load_knowledge(str(fp))

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_knowledge(str(tmp_path / "nonexistent.txt")) == ""


class TestContinuationParsing:
    def test_blank_line_ends_answer(self, tmp_path):
        """空行是答案结束符：空行后的内容不再并入上一条答案"""
        fp = tmp_path / "kb3.txt"
        _write(fp, (
            "Q: 怎么用\n"
            "A: 早晚各一次\n"
            "续行仍属于答案\n"
            "\n"
            "速查表内容不应并入答案\n"
            "\n"
            "Q: 保质期\n"
            "A: 三年\n"
        ))
        text = load_knowledge(str(fp))
        # 续行并入答案
        assert "早晚各一次\n续行仍属于答案" in text
        # 空行后的内容作为速查行原样保留，而非答案的一部分
        assert "续行仍属于答案\n速查表内容不应并入答案" not in text
        assert "速查表内容不应并入答案" in text
        # 两条问答都解析出来
        assert "问: 怎么用" in text
        assert "问: 保质期" in text

    def test_multiline_answer_without_blank_line(self, tmp_path):
        """兼容旧格式：没有空行时续行直到下一个 Q:"""
        fp = tmp_path / "kb4.txt"
        _write(fp, "Q: 一\nA: 答一\nQ: 二\nA: 答二\n")
        text = load_knowledge(str(fp))
        assert "问: 一" in text and "答一" in text
        assert "问: 二" in text and "答二" in text


class TestSystemPromptCache:
    def test_prompt_rebuilt_on_knowledge_change(self, tmp_path, monkeypatch):
        """知识库文件变更后，账号级 system prompt 缓存联动重建"""
        fp = tmp_path / "kb5.txt"
        _write(fp, "Q: 价格\nA: 100元\n", mtime=1000000.0)
        monkeypatch.setattr(
            "src.core.config.get_account",
            lambda kfid: {"system_prompt": "SP", "knowledge_file": str(fp)},
        )

        first = get_system_prompt("kf-test-hot")
        assert "100元" in first

        _write(fp, "Q: 价格\nA: 200元\n", mtime=1000100.0)
        second = get_system_prompt("kf-test-hot")
        assert "200元" in second
        assert "100元" not in second

    def test_prompt_cache_hit_when_unchanged(self, tmp_path, monkeypatch):
        """知识库未变时命中 prompt 缓存（同一对象）"""
        fp = tmp_path / "kb6.txt"
        _write(fp, "Q: 价格\nA: 100元\n")
        monkeypatch.setattr(
            "src.core.config.get_account",
            lambda kfid: {"system_prompt": "SP", "knowledge_file": str(fp)},
        )
        assert get_system_prompt("kf-test-stable") is get_system_prompt("kf-test-stable")
