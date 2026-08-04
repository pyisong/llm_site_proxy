"""从网页版助手消息里取回**原始 Markdown**，而不是渲染后的可见文字。

``inner_text()`` 拿到的是浏览器渲染结果：``##`` 标题只剩文字、表格被拍平成
制表符、代码围栏消失并混入「复制/下载」按钮文字、Mermaid 被替换成渲染 SVG 里
的节点标签。这些损坏在提取那一刻就不可逆，下游正则补不回来。

这里提供两条取回原始 Markdown 的路径：
- ``COPY_BUTTON_JS``：定位消息自带的「复制」按钮，点完读剪贴板（DeepSeek 复制
  出来的就是 Markdown 源码）。
- ``ANSWER_MARKDOWN_JS``：页面内把 DOM 反向序列化成 Markdown，作为剪贴板不可用
  （无权限 / 页面失焦）时的兜底。

``choose_answer_text`` 负责在两者与渲染文本之间做校验和取舍。
"""

from __future__ import annotations

import re

# 代码块 / Mermaid 工具栏上的按钮文字，落进正文就是脏数据
_JUNK_LABELS = (
    "复制",
    "已复制",
    "下载",
    "全屏",
    "退出全屏",
    "图表",
    "代码",
    "编辑",
    "重新生成",
    "copy",
    "copied",
    "download",
    "fullscreen",
    "chart",
    "code",
    "edit",
    "regenerate",
)

_JUNK_JS_ARRAY = "[" + ",".join(f"'{label}'" for label in _JUNK_LABELS) + "]"

# 消息自带的复制按钮：
# 1) 先认明确标注 copy/复制 的控件，避免误点「重新生成」「踩」；
# 2) DeepSeek 新版把消息工具栏做成纯图标按钮、无 aria-label/文字，这时退化为：
#    在当前助手消息最近的图标工具栏里，取最左侧按钮（实测即“复制整条消息”）。
# 万一仍点错了消息，choose_answer_text 的内容比对会把结果挡掉并退到 DOM 还原。
COPY_BUTTON_JS = (
    """(el) => {
  const WANTED = /(^|[^a-z])copy([^a-z]|$)|复制/i;
  const BAD = /(重新生成|regenerate|分享|share|踩|点赞|like|dislike|删除|delete|引用|quote)/i;
  const seen = new Set();
  const labeled = [];
  const toolbars = [];
  let scope = el;
  for (let depth = 0; depth < 8 && scope; depth++) {
    scope.querySelectorAll('[role="button"], button, [class*="copy"], [aria-label], [title]')
      .forEach((node) => {
        if (seen.has(node)) return;
        seen.add(node);
        const label = [
          node.getAttribute('aria-label') || '',
          node.getAttribute('title') || '',
          node.getAttribute('data-tooltip') || '',
          node.getAttribute('class') || '',
          (node.childElementCount === 0 ? (node.textContent || '') : ''),
        ].join(' ');
        const rect = node.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        if (BAD.test(label)) return;
        if (WANTED.test(label)) {
          labeled.push(node);
          return;
        }

        // DeepSeek 新版消息工具栏是纯图标按钮：在最近祖先里会出现一组
        // ds-button--iconLabelTertiary，且不在代码块工具栏内。
        const inCodeBlock = !!node.closest('[class*="code-block"]');
        const cls = node.getAttribute('class') || '';
        if (!inCodeBlock && /\bds-button--iconLabelTertiary\b/.test(cls)) {
          toolbars.push(node);
        }
      });
    if (labeled.length || toolbars.length) break;
    scope = scope.parentElement;
  }
  if (labeled.length) return labeled[labeled.length - 1];
  if (!toolbars.length) {
    // 全局兜底：有些页面结构下，消息工具栏不在助手正文的有限祖先范围内。
    // 这时按「离当前正文最近的 y」挑选可见图标按钮，再取最左侧。
    const elRect = el.getBoundingClientRect();
    const all = Array.from(
      document.querySelectorAll('[role="button"].ds-button--iconLabelTertiary, button.ds-button--iconLabelTertiary')
    );
    const visible = all.filter((n) => {
      if (n.closest('[class*="code-block"]')) return false;
      const r = n.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return false;
      const yDist = Math.abs(r.top - elRect.top);
      // 只在“离当前正文不太远”的按钮里选，避免误点其它浮层工具
      if (yDist > 250) return false;
      // 再加一条“只选在正文大致同一水平带”的约束
      if (r.left < elRect.left - 60) return false;
      if (r.left > elRect.left + elRect.width + 60) return false;
      return true;
    });
    visible.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      // 优先“最左侧”，再用 y 兜底：能显著减少误点右侧的其它小按钮
      if (ra.left !== rb.left) return ra.left - rb.left;
      return ra.top - rb.top;
    });
    return visible.length ? visible[0] : null;
  }

  toolbars.sort((a, b) => {
    const ra = a.getBoundingClientRect();
    const rb = b.getBoundingClientRect();
    return ra.left - rb.left || ra.top - rb.top;
  });
  return toolbars[0] || null;
}"""
)

# 渲染后的 DOM → Markdown。刻意不依赖 DeepSeek 的类名，只用 HTML 语义，
# 这样网站改版换 class 也不会失效。
ANSWER_MARKDOWN_JS = (
    """(el) => {
  const JUNK = new Set("""
    + _JUNK_JS_ARRAY
    + """.map((s) => s.toLowerCase()));
  const MERMAID_HEAD = /^(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart|gitGraph|xychart-beta)\\b/;
  const BLOCK_SEL = 'p,div,h1,h2,h3,h4,h5,h6,ul,ol,li,table,pre,blockquote,hr,section,article';
  const INLINE_TAGS = new Set(['A','B','STRONG','I','EM','CODE','SPAN','U','S','DEL','SUP','SUB','BR','IMG','MARK','SMALL','FONT','KBD','LABEL']);

  const CODE_LANGS = /^(python|py|javascript|js|typescript|ts|tsx|jsx|java|c|cpp|c\\+\\+|csharp|cs|c#|go|golang|rust|rs|ruby|rb|php|swift|kotlin|kt|scala|shell|bash|sh|zsh|powershell|ps1|sql|json|json5|yaml|yml|toml|xml|html|css|scss|sass|less|markdown|md|dockerfile|makefile|cmake|ini|properties|diff|patch|graphql|proto|protobuf|r|matlab|perl|lua|dart|haskell|hs|elixir|ex|erlang|clojure|clj|groovy|objectivec|objc|vue|svelte|solidity|mermaid|plaintext|plain|text|txt|nginx|apache|vim|asm)$/i;

  const clone = el.cloneNode(true);
  clone.querySelectorAll('script,style,noscript').forEach((n) => n.remove());

  // 1) 代码块：识别整个代码块容器（DeepSeek 用 .md-code-block，内含语言标签 +
  //    复制/下载工具栏 + <pre>），提取语言与源码后，把整块替换成干净的 <pre>，
  //    这样工具栏文字与图标 SVG 都不会漏进正文。
  Array.from(clone.querySelectorAll('pre')).forEach((pre) => {
    if (!pre.parentNode) return;
    const codeEl = pre.querySelector('code') || pre;
    const raw = codeEl.textContent || '';
    let lang = '';
    const cls = (codeEl.getAttribute('class') || '') + ' ' + (pre.getAttribute('class') || '');
    const byClass = cls.match(/language-([\\w+#.-]+)/i);
    if (byClass) lang = byClass[1];

    // 向上找「代码块容器」：类名含 code-block 即认定，最多 5 层
    let container = null;
    let probe = pre;
    for (let i = 0; i < 5 && probe.parentElement && probe.parentElement !== clone; i++) {
      probe = probe.parentElement;
      if (/code-block/i.test(probe.className || '')) { container = probe; break; }
    }

    // 语言未知时，从容器 banner 里找一个「代码语言样」的短 token（排在 pre 之前）
    if (!lang && container) {
      const nodes = Array.from(container.querySelectorAll('*'));
      for (const node of nodes) {
        if (node.contains(pre)) continue;
        const t = (node.textContent || '').trim();
        if (CODE_LANGS.test(t)) { lang = t.toLowerCase(); break; }
      }
    }
    if (!lang && MERMAID_HEAD.test(raw.trim())) lang = 'mermaid';

    const holder = document.createElement('pre');
    holder.setAttribute('data-md-lang', lang || '');
    holder.setAttribute('data-md-code', raw);
    if (container && container !== clone) {
      container.replaceWith(holder);
    } else {
      pre.replaceWith(holder);
    }
  });

  // 2) 渲染出来的图（SVG）只会贡献一堆散落的节点标签，源码已在上一步取到
  clone.querySelectorAll('svg,canvas').forEach((n) => n.remove());

  // 3) 摘掉纯按钮文字的叶子节点
  Array.from(clone.querySelectorAll('*')).forEach((node) => {
    if (node.childElementCount > 0) return;
    if (node.hasAttribute('data-md-code')) return;
    const text = (node.textContent || '').trim().toLowerCase();
    if (text && JUNK.has(text)) node.remove();
  });

  const inlineText = (node) => {
    let out = '';
    node.childNodes.forEach((ch) => {
      if (ch.nodeType === 3) { out += (ch.nodeValue || '').replace(/\\s+/g, ' '); return; }
      if (ch.nodeType !== 1) return;
      const tag = ch.tagName;
      if (tag === 'BR') { out += '\\n'; return; }
      if (tag === 'IMG') {
        const src = ch.getAttribute('src') || '';
        if (src) out += '![' + (ch.getAttribute('alt') || '') + '](' + src + ')';
        return;
      }
      if (tag === 'CODE') {
        const t = (ch.textContent || '').trim();
        if (t) out += '`' + t + '`';
        return;
      }
      if (tag === 'STRONG' || tag === 'B') {
        const t = inlineText(ch).trim();
        if (t) out += '**' + t + '**';
        return;
      }
      if (tag === 'EM' || tag === 'I') {
        const t = inlineText(ch).trim();
        if (t) out += '*' + t + '*';
        return;
      }
      if (tag === 'DEL' || tag === 'S') {
        const t = inlineText(ch).trim();
        if (t) out += '~~' + t + '~~';
        return;
      }
      if (tag === 'A') {
        const t = inlineText(ch).trim();
        const href = ch.getAttribute('href') || '';
        out += (t && href && !/^javascript:/i.test(href)) ? '[' + t + '](' + href + ')' : t;
        return;
      }
      out += inlineText(ch);
    });
    return out;
  };

  const tableMarkdown = (table) => {
    const rows = Array.from(table.querySelectorAll('tr'));
    if (!rows.length) return '';
    const grid = rows.map((tr) => Array.from(tr.children)
      .filter((c) => c.tagName === 'TD' || c.tagName === 'TH')
      .map((c) => inlineText(c).trim().replace(/\\|/g, '\\\\|').replace(/\\n+/g, ' ')));
    const width = Math.max.apply(null, grid.map((r) => r.length));
    if (!width) return '';
    const norm = grid.map((r) => { const c = r.slice(); while (c.length < width) c.push(''); return c; });
    const lines = ['| ' + norm[0].join(' | ') + ' |', '| ' + norm[0].map(() => '---').join(' | ') + ' |'];
    norm.slice(1).forEach((r) => lines.push('| ' + r.join(' | ') + ' |'));
    return lines.join('\\n');
  };

  const listMarkdown = (list, depth) => {
    const ordered = list.tagName === 'OL';
    let start = parseInt(list.getAttribute('start') || '1', 10);
    if (!isFinite(start)) start = 1;
    const pad = '  '.repeat(depth);
    const lines = [];
    Array.from(list.children).filter((c) => c.tagName === 'LI').forEach((li, idx) => {
      const marker = ordered ? (start + idx) + '. ' : '- ';
      const nested = Array.from(li.children).filter((c) => c.tagName === 'UL' || c.tagName === 'OL');
      const body = li.cloneNode(true);
      Array.from(body.children).forEach((c) => {
        if (c.tagName === 'UL' || c.tagName === 'OL') c.remove();
      });
      const text = body.querySelector('p,pre,table,blockquote')
        ? blockMarkdown(body).join('\\n\\n')
        : inlineText(body).trim();
      const parts = (text || '').split('\\n');
      lines.push(pad + marker + (parts[0] || ''));
      parts.slice(1).forEach((line) => lines.push(pad + '  ' + line));
      nested.forEach((n) => lines.push(listMarkdown(n, depth + 1)));
    });
    return lines.join('\\n');
  };

  function blockMarkdown(node) {
    const parts = [];
    node.childNodes.forEach((ch) => {
      if (ch.nodeType === 3) {
        const t = (ch.nodeValue || '').trim();
        if (t) parts.push(t);
        return;
      }
      if (ch.nodeType !== 1) return;
      const tag = ch.tagName;

      if (tag === 'PRE' && ch.hasAttribute('data-md-code')) {
        const lang = ch.getAttribute('data-md-lang') || '';
        const code = (ch.getAttribute('data-md-code') || '').replace(/\\s+$/, '');
        if (code.trim()) parts.push('```' + lang + '\\n' + code + '\\n```');
        return;
      }
      if (/^H[1-6]$/.test(tag)) {
        const t = inlineText(ch).trim();
        if (t) parts.push('#'.repeat(Number(tag[1])) + ' ' + t);
        return;
      }
      if (tag === 'P') {
        const t = inlineText(ch).trim();
        if (t) parts.push(t);
        return;
      }
      if (tag === 'BLOCKQUOTE') {
        const inner = blockMarkdown(ch).join('\\n\\n');
        if (inner.trim()) {
          parts.push(inner.split('\\n').map((line) => ('> ' + line).replace(/\\s+$/, '')).join('\\n'));
        }
        return;
      }
      if (tag === 'UL' || tag === 'OL') {
        const t = listMarkdown(ch, 0);
        if (t.trim()) parts.push(t);
        return;
      }
      if (tag === 'TABLE') {
        const t = tableMarkdown(ch);
        if (t) parts.push(t);
        return;
      }
      if (tag === 'HR') { parts.push('---'); return; }
      if (INLINE_TAGS.has(tag) || !ch.querySelector(BLOCK_SEL)) {
        const t = inlineText(ch).trim();
        if (t) parts.push(t);
        return;
      }
      blockMarkdown(ch).forEach((x) => parts.push(x));
    });
    return parts.filter((x) => x && x.trim());
  }

  return blockMarkdown(clone).join('\\n\\n').replace(/\\n{3,}/g, '\\n\\n').trim();
}"""
)

_NON_WORD = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")

# 候选相对渲染文本的最低覆盖率。DOM→Markdown 会主动丢掉按钮文字与 SVG 标签，
# 所以不能要求 1:1。
_MIN_COVER_RATIO = 0.5
_PROBE_LEN = 10
_PROBE_POSITIONS = (0.05, 0.45, 0.85)
_MIN_PROBE_HITS = 2


def normalize_for_compare(text: str) -> str:
    """只保留中英文数字，用于跨 Markdown 语法比对同一段内容。"""
    return _NON_WORD.sub("", text or "")


def candidate_covers_rendered(candidate: str | None, rendered: str) -> bool:
    """候选是否确实是这条渲染答案的原文（挡住空值、截断与过期剪贴板）。"""
    cand = (candidate or "").strip()
    if not cand:
        return False
    ref = normalize_for_compare(rendered)
    if not ref:
        return True
    got = normalize_for_compare(cand)
    if len(got) < len(ref) * _MIN_COVER_RATIO:
        return False
    hits = 0
    for position in _PROBE_POSITIONS:
        start = min(int(len(ref) * position), max(0, len(ref) - _PROBE_LEN))
        probe = ref[start : start + _PROBE_LEN]
        if probe and probe in got:
            hits += 1
    return hits >= _MIN_PROBE_HITS


def choose_answer_text(
    *,
    clipboard: str | None,
    dom_markdown: str | None,
    rendered: str,
) -> str:
    """优先剪贴板原文，其次 DOM 还原，都不可信才退回渲染文本。"""
    # DOM 还原经常会主动丢掉「复制/下载/全屏」这类 UI 垃圾（它们不会出现在真实 Markdown 源码里），
    # 因而在严格的覆盖率探测下可能误判为“不覆盖”。为避免把 ` ```python ` 等关键信息丢回 rendered_text，
    # 当 DOM 还原结果包含代码围栏时，直接优先采用它。
    if isinstance(dom_markdown, str) and "```" in dom_markdown:
        return dom_markdown.strip()

    for candidate in (clipboard, dom_markdown):
        if candidate_covers_rendered(candidate, rendered):
            return (candidate or "").strip()
    return (rendered or "").strip()
