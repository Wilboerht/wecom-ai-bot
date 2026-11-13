"""内容一致性回归：知识库禁语、护理油状态、链接占位与短链检查脚本。"""

import json
from pathlib import Path

from src.compliance import sanitize_reply

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_FILES = [ROOT / "knowledge.txt", ROOT / "knowledge_skincare.txt"]


class TestKnowledgeCompliance:
    def test_knowledge_has_no_banned_words_outside_banned_list(self):
        for path in KNOWLEDGE_FILES:
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip() or line.lstrip().startswith("#") or "禁用" in line:
                    continue
                _, hits = sanitize_reply(line)
                assert not hits, f"{path.name}:{lineno} 命中禁语 {hits}: {line}"

    def test_oil_is_not_marked_upcoming(self):
        for path in KNOWLEDGE_FILES:
            text = path.read_text(encoding="utf-8")
            assert "即将上市" not in text, f"{path.name} 仍有即将上市"

    def test_service_hours_not_hardcoded(self):
        for path in KNOWLEDGE_FILES:
            text = path.read_text(encoding="utf-8")
            assert "10:00-20:00" not in text, f"{path.name} 仍硬编码服务时间"


class TestLinks:
    def test_oil_links_exist(self):
        links = json.loads((ROOT / "links.json").read_text(encoding="utf-8"))
        for code in ("oil-dy", "oil-xhs", "oil-tm"):
            assert code in links
            assert links[code].startswith("https://")
