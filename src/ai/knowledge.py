"""
知识库加载器

从 knowledge.txt 读取品牌 FAQ, 启动时加载, 注入到 System Prompt

格式约定:
- Q: 问题 / A: 答案 组成问答对, 答案支持多行续行（A: 行之后的非空行并入上一条答案）
- 空行是答案的结束符: 空行之后的内容不再并入上一条答案（防止速查表等内容被误并）
- # 或 = 开头的行为注释/分隔线, 跳过
- 其他非空行（如产品速查表、禁语表）原样保留

缓存按文件 mtime 热加载: 文件变更后下次读取自动重新解析, 无需重启进程
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

# filepath -> (文件 mtime, 格式化后的知识文本)
_knowledge_cache: dict[str, tuple[float, str]] = {}


def load_knowledge(filepath: str = "knowledge.txt") -> str:
    """读取知识库文件, 返回格式化的知识文本, 按 (文件名, mtime) 缓存

    支持两种内容:
    1. Q:/A: 问答对 — 解析后格式化为编号列表, 空行结束上一条答案
    2. 其他非注释行 — 原样保留（如产品速查表、禁语表）
    """
    if not os.path.exists(filepath):
        logger.warning("知识库文件未找到: %s, 使用空知识库", filepath)
        return ""

    # 热加载: mtime 未变直接返回缓存, 变了则重新解析
    mtime = os.path.getmtime(filepath)
    cached = _knowledge_cache.get(filepath)
    if cached and cached[0] == mtime:
        return cached[1]

    entries = []
    raw_lines = []
    current_q = ""
    current_a = ""

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            # 空行: 结束上一条答案（续行不再并入）
            if not line:
                if current_q and current_a:
                    entries.append((current_q, current_a))
                    current_q = ""
                    current_a = ""
                continue

            # 跳过注释和分隔线
            if line.startswith("#") or line.startswith("="):
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
                # 续行（多行答案的继续, 直到空行或下一条 Q:）
                current_a += "\n" + line
            else:
                # 非问答内容（如产品速查表）— 原样保留
                raw_lines.append(line)

    # 保存最后一条
    if current_q and current_a:
        entries.append((current_q, current_a))

    if not entries and not raw_lines:
        logger.warning("知识库文件为空或无有效条目")
        return ""

    # 构建格式化知识文本
    lines = ["【品牌信息参考 — 回答问题时直接引用，不要提资料来源】"]

    # 先放原始内容（速查表等高密度信息放最前，AI 优先读）
    if raw_lines:
        lines.extend(raw_lines)
        lines.append("")

    for i, (q, a) in enumerate(entries, 1):
        lines.append(f"{i}. 问: {q}")
        lines.append(f"   答: {a}")

    text = "\n".join(lines)
    _knowledge_cache[filepath] = (mtime, text)
    logger.info("知识库加载完成 [%s]: %d 条问答, %d 行速查", filepath, len(entries), len(raw_lines))
    return text
