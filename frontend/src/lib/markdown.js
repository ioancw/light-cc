// Markdown rendering pipeline: marked + KaTeX + DOMPurify + Prism.

import { marked } from 'marked';
import DOMPurify from 'dompurify';
import katex from 'katex';
import Prism from 'prismjs';

// Explicitly import common language grammars (autoloader doesn't work in bundled apps)
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-sql';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-markdown';
import 'prismjs/components/prism-c';
import 'prismjs/components/prism-cpp';
import 'prismjs/components/prism-java';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-rust';
import 'prismjs/components/prism-jsx';
import 'prismjs/components/prism-tsx';
import 'prismjs/components/prism-toml';
import 'prismjs/components/prism-r';
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
  // Tight delimiters: opening $ must not be followed by a space/newline,
  // closing $ must not be preceded by a space. This prevents pairing two
  // unrelated currency values like "$100 ... $115" as one math block.
  processed = processed.replace(/(?<!\w)\$(?! )(\S[^\$\n]*?\S|\S)\$(?!\w)/g, (_, tex) => {
    const id = `MATHI${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: false });
    return id;
  });
  processed = processed.replace(/\\\(([\s\S]+?)\\\)/g, (_, tex) => {
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
