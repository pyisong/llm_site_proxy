import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from answer_markdown import (
    candidate_covers_rendered,
    choose_answer_text,
    normalize_for_compare,
)

RENDERED = (
    "一、为什么推理必须重新审视 KV Cache\n"
    "大模型推理的竞争，本质上是内存管理效率的竞争。\n"
    "架构\tKV Heads 数量\t典型模型\n"
    "MHA\t= num_q_heads\tGPT-2\n"
    "python\n复制\n下载\n"
    "def estimate_kv_cache(batch_size, seq_len):\n"
    "    return batch_size * seq_len\n"
)

MARKDOWN = (
    "## 一、为什么推理必须重新审视 KV Cache\n\n"
    "大模型推理的竞争，本质上是内存管理效率的竞争。\n\n"
    "| 架构 | KV Heads 数量 | 典型模型 |\n"
    "| --- | --- | --- |\n"
    "| MHA | = num_q_heads | GPT-2 |\n\n"
    "```python\n"
    "def estimate_kv_cache(batch_size, seq_len):\n"
    "    return batch_size * seq_len\n"
    "```\n"
)


def test_normalize_drops_syntax_and_whitespace():
    assert normalize_for_compare("## 标题 **粗** `code`") == "标题粗code"


def test_markdown_candidate_covers_rendered_answer():
    assert candidate_covers_rendered(MARKDOWN, RENDERED) is True


def test_stale_clipboard_is_rejected():
    stale = "上一轮完全无关的旧剪贴板内容，字数也凑得够长够长够长够长够长够长。" * 4
    assert candidate_covers_rendered(stale, RENDERED) is False


def test_truncated_candidate_is_rejected():
    assert candidate_covers_rendered(MARKDOWN[:40], RENDERED) is False


def test_empty_candidate_is_rejected():
    assert candidate_covers_rendered("", RENDERED) is False
    assert candidate_covers_rendered(None, RENDERED) is False


def test_choose_prefers_clipboard_markdown():
    assert choose_answer_text(
        clipboard=MARKDOWN,
        dom_markdown="## 兜底\n\n内容",
        rendered=RENDERED,
    ) == MARKDOWN.strip()


def test_choose_falls_back_to_dom_when_clipboard_stale():
    assert choose_answer_text(
        clipboard="毫不相关的旧内容" * 20,
        dom_markdown=MARKDOWN,
        rendered=RENDERED,
    ) == MARKDOWN.strip()


def test_choose_falls_back_to_rendered_when_both_unusable():
    assert choose_answer_text(
        clipboard=None,
        dom_markdown="",
        rendered=RENDERED,
    ) == RENDERED.strip()


def test_choose_accepts_candidate_when_rendered_is_empty():
    assert choose_answer_text(clipboard=MARKDOWN, dom_markdown=None, rendered="") == MARKDOWN.strip()
