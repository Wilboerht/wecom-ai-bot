"""
知识库加载器

从 knowledge.txt 读取品牌 FAQ, 启动时加载, 注入到 System Prompt
格式: Q: 问题   A: 答案   支持 # 注释
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

_knowledge_cache: dict[str, str] = {}


def load_knowledge(filepath: str = "knowledge.txt") -> str:
    """读取知识库文件, 返回格式化的知识文本, 按文件名缓存"""
    if filepath in _knowledge_cache:
        return _knowledge_cache[filepath]

    if not os.path.exists(filepath):
        logger.warning("知识库文件未找到: %s, 使用空知识库", filepath)
        return ""

    entries = []
    current_q = ""
    current_a = ""

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            # 跳过空行和注释
            if not line or line.startswith("#") or line.startswith("="):
                continue

            if line.startswith("Q:") or line.startswith("Q："):
                # 保存上一条
                if current_q and current_a:
                    entries.append((current_q, current_a))
                current_q = line[2:].strip()
                current_a = ""
            elif line.startswith("A:") or line.startswith("A："):
                # 收集答案（支持多行）
                if current_a:
                    current_a += "\n" + line[2:].strip()
                else:
                    current_a = line[2:].strip()
            elif current_a:
                # 续行（多行答案的继续）
                current_a += "\n" + line

    # 保存最后一条
    if current_q and current_a:
        entries.append((current_q, current_a))

    if not entries:
        logger.warning("知识库文件为空或无有效条目")
        return ""

    # 构建格式化知识文本
    lines = ["【品牌知识库 — 请基于以下内容回答用户问题】"]
    for i, (q, a) in enumerate(entries, 1):
        lines.append(f"{i}. 问: {q}")
        lines.append(f"   答: {a}")

    text = "\n".join(lines)
    _knowledge_cache[filepath] = text
    logger.info("知识库加载完成 [%s]: %d 条", filepath, len(entries))
    return text
