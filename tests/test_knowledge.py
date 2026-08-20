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

    def test_missing_file_warns_only_once(self, tmp_path, caplog):
        """文件缺失也缓存：重复读取只 warn 一次不刷屏"""
        import logging

        fp = str(tmp_path / "nonexistent2.txt")
        with caplog.at_level(logging.WARNING, logger="src.ai.knowledge"):
            assert load_knowledge(fp) == ""
            assert load_knowledge(fp) == ""
            assert load_knowledge(fp) == ""
        warns = [r for r in caplog.records if "未找到" in r.message]
        assert len(warns) == 1

    def test_missing_file_hot_loads_when_appears(self, tmp_path):
        """缺失缓存不挡热加载：文件出现后 mtime 变化自然重新解析"""
        fp = tmp_path / "kb_appear.txt"
        assert load_knowledge(str(fp)) == ""
        _write(fp, "Q: 价格\nA: 100元\n")
        assert "100元" in load_knowledge(str(fp))


class TestReadFailure:
    def test_oserror_keeps_old_cache(self, tmp_path, caplog, monkeypatch):
        """open 抛 OSError（文件被占用/权限）时沿用旧缓存并记 error"""
        import logging

        fp = tmp_path / "kb_err1.txt"
        _write(fp, "Q: 价格\nA: 100元\n", mtime=1000000.0)
        assert "100元" in load_knowledge(str(fp))

        os.utime(fp, (1000100.0, 1000100.0))  # mtime 变化绕过缓存命中

        def _raise(*args, **kwargs):
            raise OSError("file locked")

        monkeypatch.setattr("builtins.open", _raise)
        with caplog.at_level(logging.ERROR, logger="src.ai.knowledge"):
            text = load_knowledge(str(fp))
        assert "100元" in text
        assert any("读取失败" in r.message for r in caplog.records)

    def test_oserror_without_cache_returns_empty(self, tmp_path, caplog, monkeypatch):
        """无缓存时 open 异常返回空知识库并记 error"""
        import logging

        fp = tmp_path / "kb_err2.txt"
        _write(fp, "Q: 价格\nA: 100元\n")

        def _raise(*args, **kwargs):
            raise OSError("file locked")

        monkeypatch.setattr("builtins.open", _raise)
        with caplog.at_level(logging.ERROR, logger="src.ai.knowledge"):
            assert load_knowledge(str(fp)) == ""
        assert any("读取失败" in r.message for r in caplog.records)

    def test_empty_parse_keeps_old_cache(self, tmp_path, caplog):
        """半写入防护：解析结果为空（可能读到写了一半的文件）时沿用旧缓存"""
        import logging

        fp = tmp_path / "kb_half.txt"
        _write(fp, "Q: 价格\nA: 100元\n", mtime=1000000.0)
        assert "100元" in load_knowledge(str(fp))

        # 只写了半条（有 Q 无 A），解析结果为空
        _write(fp, "Q: 价格\n", mtime=1000100.0)
        with caplog.at_level(logging.WARNING, logger="src.ai.knowledge"):
            text = load_knowledge(str(fp))
        assert "100元" in text
        assert any("解析结果为空" in r.message for r in caplog.records)


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
