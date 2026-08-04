"""用真实 Chromium 验证 ANSWER_MARKDOWN_JS：渲染后的 DOM 能还原成 Markdown。

容器内直接运行（无需 pytest）：
    python tests/verify_answer_markdown_dom.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from answer_markdown import ANSWER_MARKDOWN_JS

# 复刻 DeepSeek 网页渲染后的助手消息：标题 / 表格 / 带工具栏的代码块 /
# Mermaid（工具栏 + 渲染 SVG + 隐藏源码）/ 列表 / 引用 / 行内样式。
FIXTURE_HTML = """
<div class="ds-assistant-message-main-content">
  <h1>大模型推理的命脉：KV Cache</h1>
  <blockquote><p>KV Cache 的管理策略直接决定吞吐与延迟边界。</p></blockquote>
  <h2>一、为什么推理必须重新审视 KV Cache</h2>
  <p>大模型推理的竞争，本质上是<strong>内存管理效率</strong>的竞争，参数见 <code>gpu_memory_utilization</code>。</p>
  <ul>
    <li>训练是<em>离线</em>吞吐导向的批处理</li>
    <li>推理是在线延迟导向的服务
      <ul><li>P99 延迟超 50ms 即告警</li></ul>
    </li>
  </ul>
  <h3>1.1 注意力架构与 KV 大小</h3>
  <table>
    <thead><tr><th>架构</th><th>KV Heads 数量</th><th>典型模型</th></tr></thead>
    <tbody>
      <tr><td>MHA</td><td>= num_q_heads</td><td>GPT-2, BERT</td></tr>
      <tr><td>GQA</td><td>= num_q_heads / 分组数</td><td>Llama-2/3</td></tr>
    </tbody>
  </table>
  <div class="md-code-block md-code-block-light">
    <div class="md-code-block-banner-wrap">
      <div class="md-code-block-banner md-code-block-banner-lite">
        <div class="_121d384">
          <div class="d2a24f03"><span class="d813de27">python</span></div>
          <div class="d2a24f03 _246a029">
            <div role="button" class="ds-button ds-button--xs">
              <div class="ds-button__icon"><svg><path d="M0 0"></path></svg></div>
              <span class="ds-button__content"><span class="code-info-button-text">复制</span></span>
            </div>
            <div role="button" class="ds-button ds-button--xs">
              <div class="ds-button__icon"><svg><path d="M0 0"></path></svg></div>
              <span class="ds-button__content"><span class="code-info-button-text">下载</span></span>
            </div>
          </div>
        </div>
      </div>
    </div>
    <pre><code>def estimate_kv_cache(batch_size, seq_len, num_layers):
    return batch_size * seq_len * 2 * num_layers</code></pre>
  </div>
  <h2>二、PagedAttention</h2>
  <div class="md-mermaid">
    <div class="md-mermaid-banner">
      <div role="button">图表</div><div role="button">代码</div>
      <div role="button">下载</div><div role="button">全屏</div>
    </div>
    <div class="mermaid-render">
      <svg><g><text>物理显存</text><text>物理Block5</text><text>Block Table</text>
      <text>逻辑Block0→物理5</text></g></svg>
    </div>
    <pre style="display:none"><code class="language-mermaid">graph TD
    A[逻辑Block0] --> B[物理Block5]</code></pre>
  </div>
  <p>参考 <a href="https://arxiv.org/abs/2309.06180">PagedAttention 论文</a>。</p>
  <hr>
  <p>显存利用率 60%→95%+。</p>
</div>
"""

MUST_CONTAIN = [
    "# 大模型推理的命脉：KV Cache",
    "## 一、为什么推理必须重新审视 KV Cache",
    "### 1.1 注意力架构与 KV 大小",
    "## 二、PagedAttention",
    "> KV Cache 的管理策略直接决定吞吐与延迟边界。",
    "**内存管理效率**",
    "`gpu_memory_utilization`",
    "*离线*",
    "- 训练是",
    "  - P99 延迟超 50ms 即告警",
    "| 架构 | KV Heads 数量 | 典型模型 |",
    "| --- | --- | --- |",
    "| MHA | = num_q_heads | GPT-2, BERT |",
    "```python",
    "def estimate_kv_cache(batch_size, seq_len, num_layers):",
    "    return batch_size * seq_len * 2 * num_layers",
    "```mermaid",
    "graph TD",
    "[PagedAttention 论文](https://arxiv.org/abs/2309.06180)",
    "---",
]

# 网页 UI 残留 + Mermaid 渲染后的 SVG 标签，绝不能作为裸正文出现。
# 只校验代码围栏之外——节点名出现在还原出的 mermaid 源码里是正确结果。
MUST_NOT_CONTAIN_OUTSIDE_FENCES = [
    "复制",
    "下载",
    "全屏",
    "图表",
    "物理Block5",
    "Block Table",
    "逻辑Block0→物理5",
]


def strip_fenced_blocks(markdown: str) -> str:
    return re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(FIXTURE_HTML)
        markdown = page.eval_on_selector(".ds-assistant-message-main-content", ANSWER_MARKDOWN_JS)
        browser.close()

    print("--- 转换结果 ---")
    print(markdown)
    print("--- 校验 ---")

    failures: list[str] = []
    for needle in MUST_CONTAIN:
        if needle not in markdown:
            failures.append(f"缺失: {needle!r}")
    prose = strip_fenced_blocks(markdown)
    for needle in MUST_NOT_CONTAIN_OUTSIDE_FENCES:
        if needle in prose:
            failures.append(f"围栏外残留: {needle!r}")

    # mermaid 源码必须在 ```mermaid 围栏内，而不是散落的裸文本
    if "```mermaid" in markdown:
        block = markdown.split("```mermaid", 1)[1]
        if "graph TD" not in block.split("```", 1)[0]:
            failures.append("mermaid 源码不在围栏内")

    if failures:
        for line in failures:
            print(f"FAIL {line}")
        print(f"\n{len(failures)} 项不通过")
        return 1

    print(f"OK 全部 {len(MUST_CONTAIN) + len(MUST_NOT_CONTAIN_OUTSIDE_FENCES)} 项通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
