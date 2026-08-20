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

# filepath -> (文件指纹 (mtime, size), 格式化后的知识文本)；文件缺失时指纹记 None 表示空知识库
# 双指纹防止"编辑器同秒两段写入"时截断的非空内容以相同 mtime 覆盖完整版缓存
_knowledge_cache: dict[str, tuple[tuple[float, int] | None, str]] = {}

# filepath -> (文件指纹, 常驻段文本, {产品名: 产品FAQ段文本})；按需注入模式（knowledge_split）专用
_knowledge_parts_cache: dict[str, tuple[tuple[float, int] | None, str, dict[str, str]]] = {}

# 产品FAQ段标题行，如：# --- 产品FAQ：云朵洁面 ---
_SECTION_HEADER_RE = re.compile(r"^#\s*-{2,}\s*产品FAQ[:：]\s*(.+?)\s*-{2,}\s*$")


def _parse_knowledge_items(lines) -> list:
    """把知识库原始行解析为有序条目列表。

    条目类型:
    - ("section", 段名)          — 产品FAQ段标题（常驻/按需拆分的分界）
    - ("raw", 行文本)            — 非问答内容（速查表、禁语表等）原样保留
    - ("qa", 问题, 答案)         — 问答对，答案支持多行续行，空行结束
    """
    items: list = []
    current_q = ""
    current_a = ""

    for line in lines:
        line = line.strip()

        # 空行: 结束上一条答案（续行不再并入）
        if not line:
            if current_q and current_a:
                items.append(("qa", current_q, current_a))
                current_q = ""
                current_a = ""
            continue

        # 产品FAQ段标题（先于普通注释判断，作为按需注入的段分界）
        m = _SECTION_HEADER_RE.match(line)
        if m:
            if current_q and current_a:
                items.append(("qa", current_q, current_a))
                current_q = ""
                current_a = ""
            items.append(("section", m.group(1).strip()))
            continue

        # 跳过注释和分隔线
        if line.startswith("#") or line.startswith("="):
            continue

        if line.startswith("Q:") or line.startswith("Q："):
            # 保存上一条
            if current_q and current_a:
                items.append(("qa", current_q, current_a))
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
            items.append(("raw", line))

    # 保存最后一条
    if current_q and current_a:
        items.append(("qa", current_q, current_a))
    return items


def load_knowledge(filepath: str = "knowledge.txt") -> str:
    """读取知识库文件, 返回格式化的知识文本, 按 (文件名, mtime) 缓存

    支持两种内容:
    1. Q:/A: 问答对 — 解析后格式化为编号列表, 空行结束上一条答案
    2. 其他非注释行 — 原样保留（如产品速查表、禁语表）
    """
    if not os.path.exists(filepath):
        # 缺失也缓存（mtime 记 None）：只 warn 一次不刷屏；文件出现后走热加载
        cached = _knowledge_cache.get(filepath)
        if cached and cached[0] is None:
            return cached[1]
        logger.warning("知识库文件未找到: %s, 使用空知识库", filepath)
        _knowledge_cache[filepath] = (None, "")
        return ""

    # 热加载: 指纹 (mtime, size) 未变直接返回缓存, 变了则重新解析
    st = os.stat(filepath)
    fingerprint = (st.st_mtime, st.st_size)
    cached = _knowledge_cache.get(filepath)
    if cached and cached[0] == fingerprint:
        return cached[1]

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            items = _parse_knowledge_items(f)
    except OSError as e:
        # 文件被占用/权限异常：有旧缓存沿用旧缓存，无缓存返回空知识库
        cached = _knowledge_cache.get(filepath)
        if cached:
            logger.error("知识库读取失败: %s（沿用旧缓存）", e)
            return cached[1]
        logger.error("知识库读取失败: %s（无缓存，使用空知识库）", e)
        return ""

    if not items:
        # 解析结果为空可能是文件写到一半被读到：有旧缓存沿用，不用空结果覆盖
        cached = _knowledge_cache.get(filepath)
        if cached:
            logger.warning("知识库解析结果为空（文件可能正在写入），沿用旧缓存: %s", filepath)
            return cached[1]
        logger.warning("知识库文件为空或无有效条目")
        return ""

    # 构建格式化知识文本
    lines = ["【品牌信息参考 — 回答问题时直接引用，不要提资料来源】"]

    # 先放原始内容（速查表等高密度信息放最前，AI 优先读）；段标题不参与全量拼接
    raw_lines = [item[1] for item in items if item[0] == "raw"]
    if raw_lines:
        lines.extend(raw_lines)
        lines.append("")

    i = 0
    for item in items:
        if item[0] == "qa":
            i += 1
            lines.append(f"{i}. 问: {item[1]}")
            lines.append(f"   答: {item[2]}")

    text = "\n".join(lines)
    _knowledge_cache[filepath] = (fingerprint, text)
    n_qa = sum(1 for it in items if it[0] == "qa")
    logger.info("知识库加载完成 [%s]: %d 条问答, %d 行速查", filepath, n_qa, len(raw_lines))
    return text


def load_knowledge_parts(filepath: str = "knowledge.txt") -> tuple[str, dict[str, str]]:
    """按「常驻段 + 产品FAQ按需段」拆分加载知识库，(mtime, size) 指纹热加载。

    常驻段：产品FAQ段之外的全部内容（品牌/售后/禁语/产品线清单等，每请求必带）。
    按需段：每个「# --- 产品FAQ：XXX ---」段独立成块，由调用方按关键词命中后选择性注入。
    知识库没有产品FAQ段时按需段为空，等价于全量加载。

    Returns: (base_text, {产品名: 段文本})
    """
    if not os.path.exists(filepath):
        cached = _knowledge_parts_cache.get(filepath)
        if cached and cached[0] is None:
            return cached[1], cached[2]
        logger.warning("知识库文件未找到: %s, 使用空知识库", filepath)
        _knowledge_parts_cache[filepath] = (None, "", {})
        return "", {}

    st = os.stat(filepath)
    fingerprint = (st.st_mtime, st.st_size)
    cached = _knowledge_parts_cache.get(filepath)
    if cached and cached[0] == fingerprint:
        return cached[1], cached[2]

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            items = _parse_knowledge_items(f)
    except OSError as e:
        cached = _knowledge_parts_cache.get(filepath)
        if cached:
            logger.error("知识库读取失败: %s（沿用旧缓存）", e)
            return cached[1], cached[2]
        logger.error("知识库读取失败: %s（无缓存，使用空知识库）", e)
        return "", {}

    if not items:
        cached = _knowledge_parts_cache.get(filepath)
        if cached:
            logger.warning("知识库解析结果为空（文件可能正在写入），沿用旧缓存: %s", filepath)
            return cached[1], cached[2]
        logger.warning("知识库文件为空或无有效条目")
        return "", {}

    def _fmt(section: str, part: list) -> str:
        out = [f"【产品FAQ：{section}】"] if section else ["【品牌信息参考 — 回答问题时直接引用，不要提资料来源】"]
        raws = [item[1] for item in part if item[0] == "raw"]
        if raws:
            out.extend(raws)
            out.append("")
        i = 0
        for item in part:
            if item[0] == "qa":
                i += 1
                out.append(f"{i}. 问: {item[1]}")
                out.append(f"   答: {item[2]}")
        return "\n".join(out)

    # 按段标题切分：段标题之前归常驻段，之后各归对应产品段
    groups: list[tuple[str, list]] = [("", [])]
    for item in items:
        if item[0] == "section":
            groups.append((item[1], []))
        else:
            groups[-1][1].append(item)

    base = _fmt("", groups[0][1]) if groups else ""
    sections: dict[str, str] = {}
    for name, part in groups[1:]:
        sections[name] = _fmt(name, part)

    _knowledge_parts_cache[filepath] = (fingerprint, base, sections)
    logger.info("知识库分段加载完成 [%s]: 常驻 %d 字符, %d 个按需段", filepath, len(base), len(sections))
    return base, sections
