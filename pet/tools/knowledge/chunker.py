"""轻量文本分块器 — 按段落 + 字数窗口切分。"""

import re


def chunk_text(text: str, max_chars: int = 500, overlap: int = 50) -> list[str]:
    """按段落自然边界切分，每块不超过 max_chars 字符，相邻块有 overlap 重叠。

    策略:
    1. 先按双换行拆段落
    2. 累积段落直到接近 max_chars
    3. 超长段落硬切分
    """
    if not text or not text.strip():
        return []

    # 防止 overlap >= max_chars 导致 range 步长为 0 或负数
    overlap = max(0, min(overlap, max_chars - 1))

    paragraphs = re.split(r'\n\s*\n', text.strip())
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 超长段落直接硬切
        if len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), max_chars - overlap):
                chunks.append(para[i:i + max_chars])
                if i + max_chars >= len(para):
                    break
            continue

        if len(current) + len(para) + 1 > max_chars:
            if current:
                chunks.append(current)
                # 保留 overlap 字符的上下文衔接
                current = current[-overlap:] + "\n" + para if overlap > 0 else para
            else:
                current = para
        else:
            current = current + "\n" + para if current else para

    if current:
        chunks.append(current)

    return [c for c in chunks if len(c.strip()) >= 10]
