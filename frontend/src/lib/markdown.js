// Markdown rendering pipeline: marked + KaTeX + DOMPurify + Prism.

import { marked } from 'marked';
import DOMPurify from 'dompurify';
import katex from 'katex';
import Prism from 'prismjs';
import 'prismjs/plugins/autoloader/prism-autoloader.js';
import { escapeHtml } from './utils.js';

// Configure marked
marked.use({
  gfm: true,
  breaks: true,
  renderer: {
    code({ text, lang }) {
      const language = lang || 'code';
      const langClass = lang ? ` class="language-${lang}"` : '';
      return `<pre><div class="code-block-header"><span>${escapeHtml(language)}</span><button class="copy-btn" data-code="${escapeHtml(text).replace(/"/g, '&quot;')}">copy</button></div><code${langClass}>${escapeHtml(text)}</code></pre>`;
    },
    link({ href, title, tokens }) {
      const text = this.parser.parseInline(tokens);
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
      return `<a href="${escapeHtml(href)}"${titleAttr} target="_blank" rel="noopener">${text}</a>`;
    },
  },
});

export function renderMarkdown(text) {
  const mathBlocks = [];
  let processed = text;

  // Extract display math: $$...$$ and \[...\]
  processed = processed.replace(/\$\$([\s\S]*?)\$\$/g, (_, tex) => {
    const id = `MATHD${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: true });
    return id;
  });
  processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, (_, tex) => {
    const id = `MATHD${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: true });
    return id;
  });

  // Extract inline math: $...$ and \(...\)
  processed = processed.replace(/\$([^\$\n]+?)\$/g, (_, tex) => {
    const id = `MATHI${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: false });
    return id;
  });
  processed = processed.replace(/\\\(([^)]+?)\\\)/g, (_, tex) => {
    const id = `MATHI${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: false });
    return id;
  });

  // Parse with marked
  let html = marked.parse(processed);

  // Sanitize
  html = DOMPurify.sanitize(html, {
    ADD_TAGS: ['iframe'],
    ADD_ATTR: ['target', 'rel', 'data-code', 'data-embed-tool', 'data-embed-idx', 'sandbox', 'class'],
  });

  // Restore math blocks
  for (let i = 0; i < mathBlocks.length; i++) {
    const { tex, display } = mathBlocks[i];
    const placeholder = display ? `MATHD${i}MATHEND` : `MATHI${i}MATHEND`;
    let rendered;
    try {
      rendered = katex.renderToString(tex, { displayMode: display, throwOnError: false, trust: true });
      rendered = display
        ? `<div class="math-display">${rendered}</div>`
        : `<span class="math-inline">${rendered}</span>`;
    } catch {
      rendered = display
        ? `<div class="math-display"><code>${escapeHtml(tex)}</code></div>`
        : `<code>${escapeHtml(tex)}</code>`;
    }
    html = html.replace(placeholder, rendered);
  }

  return html;
}

export function highlightCode(container) {
  if (Prism) {
    Prism.highlightAllUnder(container);
  }
}
