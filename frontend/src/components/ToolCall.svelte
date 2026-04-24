<script>
  import Chart from './renderers/Chart.svelte';
  import Image from './renderers/Image.svelte';
  import Table from './renderers/Table.svelte';
  import HtmlEmbed from './renderers/HtmlEmbed.svelte';
  import { getToolBadge } from '../lib/toolBadge.js';

  let { tc } = $props();
  let expanded = $state(false);
  let outputExpanded = $state(false);

  function toggle() {
    expanded = !expanded;
  }

  function toggleOutput() {
    outputExpanded = !outputExpanded;
  }

  let badge = $derived(getToolBadge(tc.name));

  let streamText = $derived(tc.streamBuffer || '');

  // Parse result JSON safely
  let parsed = $derived.by(() => {
    if (!tc.result) return null;
    const raw = typeof tc.result === 'string' ? tc.result : JSON.stringify(tc.result);
    try { return JSON.parse(raw); }
    catch { return { _raw: raw }; }
  });

  // Detect error results: explicit backend flag, the status set in ws.js, or a parsed error field.
  let isError = $derived(
    tc.is_error === true
      || tc.status === 'error'
      || (parsed && typeof parsed.error === 'string')
  );

  // Tool-specific header summary (shown in collapsed header)
  let headerSummary = $derived.by(() => {
    const n = tc.name?.toLowerCase();
    const inp = tc.input || {};
    if (n === 'read') return inp.file_path ? shortenPath(inp.file_path) : '';
    if (n === 'bash') return inp.command ? truncate(inp.command, 60) : '';
    if (n === 'edit') return inp.file_path ? shortenPath(inp.file_path) : '';
    if (n === 'write') return inp.file_path ? shortenPath(inp.file_path) : '';
    if (n === 'grep') return inp.pattern ? truncate(inp.pattern, 40) : '';
    if (n === 'glob') return inp.pattern ? truncate(inp.pattern, 40) : '';
    if (n === 'task') {
      const sub = inp.subagent_type || 'subagent';
      const desc = inp.description ? ` — ${truncate(inp.description, 40)}` : '';
      return `${sub}${desc}`;
    }
    return '';
  });

  // One-line result preview shown in collapsed header
  let resultPreview = $derived.by(() => {
    if (!parsed || tc.status === 'running') return '';
    if (isError) return parsed.error ? truncate(parsed.error, 40) : 'error';
    const n = tc.name?.toLowerCase();
    if (n === 'bash') {
      const code = parsed.exit_code;
      if (code === 0) return 'exit 0';
      return `exit ${code ?? '?'}`;
    }
    if (n === 'read') return parsed.total_lines ? `${parsed.total_lines} lines` : 'ok';
    if (n === 'grep') {
      const count = parsed.count ?? parsed.matches?.length ?? 0;
      return `${count} match${count !== 1 ? 'es' : ''}`;
    }
    if (n === 'glob') {
      const total = parsed.total ?? parsed.files?.length ?? 0;
      return `${total} file${total !== 1 ? 's' : ''}`;
    }
    if (n === 'edit') return parsed.status === 'ok' ? `${parsed.replacements} replaced` : '';
    if (n === 'write') return parsed.status === 'ok' ? `${parsed.bytes}B` : '';
    if (n === 'task') {
      if (parsed.run_id) return `run ${parsed.run_id.slice(0, 8)}`;
      if (parsed.agent_id) return 'delegated';
      return 'done';
    }
    return 'done';
  });

  function shortenPath(p) {
    if (!p) return '';
    const parts = p.replace(/\\/g, '/').split('/');
    if (parts.length <= 3) return parts.join('/');
    return '.../' + parts.slice(-2).join('/');
  }

  function truncate(s, max) {
    return s.length > max ? s.slice(0, max) + '...' : s;
  }

  function guessLang(filePath) {
    if (!filePath) return '';
    const ext = filePath.split('.').pop()?.toLowerCase();
    const map = {
      js: 'javascript', ts: 'typescript', jsx: 'jsx', tsx: 'tsx',
      py: 'python', rs: 'rust', go: 'go', java: 'java',
      c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
      css: 'css', html: 'html', json: 'json', yaml: 'yaml', yml: 'yaml',
      toml: 'toml', md: 'markdown', sql: 'sql', sh: 'bash', bash: 'bash',
      svelte: 'html', vue: 'html', xml: 'markup',
    };
    return map[ext] || '';
  }

  // Raw fallback for unknown tools
  let rawResultText = $derived.by(() => {
    if (!tc.result) return '';
    if (typeof tc.result === 'string') return tc.result;
    return JSON.stringify(tc.result, null, 2);
  });

  function copyText(text) {
    navigator.clipboard.writeText(text);
  }

  let paramRows = $derived.by(() => {
    const inp = tc.input;
    if (!inp || typeof inp !== 'object') return [];
    return Object.entries(inp).map(([k, v]) => {
      let display;
      if (v === null || v === undefined) display = '';
      else if (typeof v === 'string') display = v;
      else {
        try { display = JSON.stringify(v); }
        catch { display = String(v); }
      }
      return [k, display];
    });
  });

  let shortDescription = $derived.by(() => {
    if (!tc.description) return '';
    const firstSentence = tc.description.split(/(?<=\.)\s/)[0] || tc.description;
    return truncate(firstSentence, 240);
  });
</script>

<div
  class="tool-block tool-cat-{badge.cls}"
  class:expanded
  class:errored={isError}
  class:running={tc.status === 'running'}
  style:--tool-color={badge.color}
  role="region"
  aria-label="{tc.name} tool call"
>
  <button class="tool-header" onclick={toggle} aria-expanded={expanded}>
    <span class="tool-glyph" aria-hidden="true">
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
        <path d={badge.glyph} stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </span>
    <span class="tool-status-dot" class:running={tc.status === 'running'} class:done={tc.status === 'done'} class:error={tc.status === 'error'}></span>
    <span class="tool-name">{tc.name}</span>
    {#if headerSummary}
      <span class="tool-summary">{headerSummary}</span>
    {/if}
    {#if resultPreview}
      <span class="tool-result-preview" class:error={isError}>{resultPreview}</span>
    {/if}
    {#if tc.duration}
      <span class="tool-duration">{tc.duration}s</span>
    {/if}
    <svg class="tool-chevron" width="8" height="8" viewBox="0 0 10 10" fill="none" aria-hidden="true">
      <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </button>

  <div class="tool-body">
    {#if shortDescription}
      <div class="tool-section">
        <div class="tool-section-label"><span>Description</span></div>
        <div class="tool-description">{shortDescription}</div>
      </div>
    {/if}

    {#if paramRows.length > 0}
      <div class="tool-section">
        <div class="tool-section-label"><span>Params</span></div>
        <div class="tool-params">
          {#each paramRows as [key, value]}
            <div class="tool-param-row">
              <span class="tool-param-key">{key}</span>
              <span class="tool-param-value">{value}</span>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    {#if streamText && tc.status === 'running'}
      <div class="tool-section">
        <div class="tool-section-label"><span>Live Output</span></div>
        <div class="tool-code streaming">{streamText}</div>
      </div>
    {/if}

    <!-- Error result -->
    {#if isError}
      <div class="tool-section">
        <div class="tool-error-block">
          <span class="tool-error-label">Error</span>
          <span class="tool-error-msg">{parsed.error}</span>
        </div>
      </div>

    <!-- Read tool: file content with line numbers -->
    {:else if tc.name?.toLowerCase() === 'read' && parsed && parsed.content}
      <div class="tool-section">
        <div class="tool-section-label">
          <span>{tc.input?.file_path || 'File'}{parsed.showing ? ` (lines ${parsed.showing})` : ''}</span>
          <button class="tool-copy-btn" onclick={(e) => { e.stopPropagation(); copyText(parsed.content); }}>copy</button>
        </div>
        <div class="tool-file-content" class:output-full={outputExpanded} data-lang={guessLang(tc.input?.file_path)}>{parsed.content}</div>
        {#if parsed.total_lines}
          <div class="tool-meta">{parsed.total_lines} lines total</div>
        {/if}
      </div>

    <!-- Bash tool: command + stdout/stderr -->
    {:else if tc.name?.toLowerCase() === 'bash' && parsed && (parsed.stdout !== undefined || parsed.stderr !== undefined)}
      {#if tc.input?.command}
        <div class="tool-section">
          <div class="tool-section-label"><span>Command</span></div>
          <div class="tool-bash-cmd">$ {tc.input.command}</div>
        </div>
      {/if}
      {#if parsed.stdout}
        <div class="tool-section">
          <div class="tool-section-label">
            <span>stdout</span>
            <button class="tool-copy-btn" onclick={(e) => { e.stopPropagation(); copyText(parsed.stdout); }}>copy</button>
          </div>
          <div class="tool-code tool-result-ok" class:output-full={outputExpanded}>{parsed.stdout}</div>
        </div>
      {/if}
      {#if parsed.stderr}
        <div class="tool-section">
          <div class="tool-section-label"><span>stderr</span></div>
          <div class="tool-code tool-stderr" class:output-full={outputExpanded}>{parsed.stderr}</div>
        </div>
      {/if}
      <div class="tool-meta">
        exit code: <span class:exit-ok={parsed.exit_code === 0} class:exit-fail={parsed.exit_code !== 0}>{parsed.exit_code ?? '?'}</span>
      </div>

    <!-- Grep tool: match list -->
    {:else if tc.name?.toLowerCase() === 'grep' && parsed && Array.isArray(parsed.matches)}
      <div class="tool-section">
        <div class="tool-section-label">
          <span>{parsed.count ?? parsed.matches.length} match{(parsed.count ?? parsed.matches.length) !== 1 ? 'es' : ''}{tc.input?.pattern ? ` for "${tc.input.pattern}"` : ''}</span>
          <button class="tool-copy-btn" onclick={(e) => { e.stopPropagation(); copyText(parsed.matches.map(m => `${m.file}:${m.line}`).join('\n')); }}>copy</button>
        </div>
        {#if parsed.matches.length > 0}
          <div class="tool-match-list" class:output-full={outputExpanded}>
            {#each parsed.matches as m}
              <div class="tool-match-row">
                <span class="tool-match-file">{shortenPath(m.file)}<span class="tool-match-line">:{m.line}</span></span>
                <span class="tool-match-content">{m.content}</span>
              </div>
            {/each}
          </div>
        {:else}
          <div class="tool-empty">No matches found.</div>
        {/if}
      </div>

    <!-- Glob tool: file list -->
    {:else if tc.name?.toLowerCase() === 'glob' && parsed && Array.isArray(parsed.files)}
      <div class="tool-section">
        <div class="tool-section-label">
          <span>{parsed.total ?? parsed.files.length} file{(parsed.total ?? parsed.files.length) !== 1 ? 's' : ''}{tc.input?.pattern ? ` matching "${tc.input.pattern}"` : ''}</span>
          <button class="tool-copy-btn" onclick={(e) => { e.stopPropagation(); copyText(parsed.files.join('\n')); }}>copy</button>
        </div>
        {#if parsed.files.length > 0}
          <div class="tool-file-list" class:output-full={outputExpanded}>
            {#each parsed.files as f}
              <div class="tool-file-row">{shortenPath(f)}</div>
            {/each}
            {#if parsed.total > parsed.files.length}
              <div class="tool-meta">...and {parsed.total - parsed.files.length} more</div>
            {/if}
          </div>
        {:else}
          <div class="tool-empty">No files matched.</div>
        {/if}
      </div>

    <!-- Edit tool: diff-like view -->
    {:else if tc.name?.toLowerCase() === 'edit' && parsed && parsed.status === 'ok'}
      <div class="tool-section">
        <div class="tool-section-label">
          <span>{tc.input?.file_path ? shortenPath(tc.input.file_path) : 'Edit'}</span>
        </div>
        {#if tc.input?.old_string && tc.input?.new_string}
          <div class="tool-diff">
            <div class="tool-diff-del">{tc.input.old_string}</div>
            <div class="tool-diff-add">{tc.input.new_string}</div>
          </div>
        {/if}
        <div class="tool-meta">{parsed.replacements} replacement{parsed.replacements !== 1 ? 's' : ''} made</div>
      </div>

    <!-- Task tool: subagent delegation result -->
    {:else if tc.name?.toLowerCase() === 'task' && parsed && (parsed.result !== undefined || parsed.agent_id)}
      <div class="tool-section">
        <div class="tool-section-label"><span>Delegated to {tc.input?.subagent_type || 'subagent'}</span></div>
        {#if tc.input?.prompt}
          <div class="tool-task-prompt">{tc.input.prompt}</div>
        {/if}
      </div>
      {#if parsed.result}
        <div class="tool-section">
          <div class="tool-section-label">
            <span>Response</span>
            <button class="tool-copy-btn" onclick={(e) => { e.stopPropagation(); copyText(parsed.result); }}>copy</button>
          </div>
          <div class="tool-task-result" class:output-full={outputExpanded}>{parsed.result}</div>
        </div>
      {/if}
      <div class="tool-meta">
        {#if parsed.run_id}run: {parsed.run_id.slice(0, 8)}{/if}
        {#if parsed.agent_id} · agent: {parsed.agent_id.slice(0, 8)}{/if}
      </div>

    <!-- Write tool: clean status -->
    {:else if tc.name?.toLowerCase() === 'write' && parsed && parsed.status === 'ok'}
      <div class="tool-section">
        <div class="tool-section-label"><span>Written</span></div>
        <div class="tool-meta">{shortenPath(parsed.path)} ({parsed.bytes} bytes)</div>
      </div>

    <!-- Fallback: raw output -->
    {:else if tc.result}
      <div class="tool-section">
        <div class="tool-section-label">
          <span>Output</span>
          <button class="tool-copy-btn" onclick={(e) => { e.stopPropagation(); copyText(rawResultText); }}>copy</button>
        </div>
        <div class="tool-code tool-result-ok" class:output-full={outputExpanded}>{rawResultText}</div>
      </div>
    {/if}

    <!-- Charts, images, and tables render inline in MessageBubble -->

    {#if tc.embeds && tc.embeds.length > 0}
      <div class="tool-section">
        {#each tc.embeds as embed}
          <HtmlEmbed html={embed.html} name={embed.name} />
        {/each}
      </div>
    {/if}

    {#if tc.result && !isError}
      <button class="tool-expand-btn" onclick={(e) => { e.stopPropagation(); toggleOutput(); }}>
        {outputExpanded ? 'collapse' : 'expand'}
      </button>
    {/if}
  </div>
</div>

<style>
  .tool-block {
    border: none;
    border-left: 2px solid color-mix(in srgb, var(--tool-color, var(--border2)) 45%, transparent);
    border-radius: 0;
    overflow: hidden;
    background: transparent;
    font-family: var(--font-mono);
    padding-left: 8px;
    margin-left: -2px;
    transition: border-color 0.15s ease, background 0.15s ease;
  }
  .tool-block:hover { border-left-color: var(--tool-color, var(--accent-soft)); }
  .tool-block.expanded { border-left-color: var(--tool-color, var(--accent)); }
  .tool-block.errored {
    border-left-color: var(--red);
    background: color-mix(in srgb, var(--red) 6%, transparent);
  }
  .tool-block.errored .tool-name,
  .tool-block.errored .tool-glyph { color: var(--red); }

  .tool-glyph {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    flex-shrink: 0;
    color: var(--tool-color, var(--muted));
    background: color-mix(in srgb, var(--tool-color, var(--muted)) 10%, transparent);
    border-radius: 4px;
    transition: background 0.15s ease, color 0.15s ease;
  }
  .tool-block.expanded .tool-glyph {
    background: color-mix(in srgb, var(--tool-color, var(--accent)) 18%, transparent);
  }
  .tool-block.running .tool-glyph {
    animation: tool-glyph-pulse 1.8s ease-in-out infinite;
  }
  @keyframes tool-glyph-pulse {
    0%, 100% { opacity: 0.75; }
    50% { opacity: 1; }
  }

  .tool-header {
    padding: 6px 4px;
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    transition: background 0.15s ease;
    width: 100%;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    min-height: 32px;
  }
  .tool-header:hover { background: var(--surface2); border-radius: 4px; }

  .tool-status-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .tool-status-dot.running {
    background: var(--amber);
    animation: dot-pulse 1.2s ease-in-out infinite;
  }
  .tool-status-dot.done {
    background: var(--green);
  }
  .tool-status-dot.error {
    background: var(--red);
  }
  @keyframes dot-pulse {
    0%, 100% { opacity: 0.3; }
    50% { opacity: 1; }
  }

  .tool-name {
    font-size: 12px;
    font-weight: 500;
    color: var(--fg-dim);
  }

  .tool-result-preview {
    font-size: 12px;
    color: var(--muted);
    flex-shrink: 0;
  }
  .tool-result-preview.error {
    color: var(--red);
  }

  .tool-duration {
    margin-left: auto;
    font-size: 12px;
    color: var(--muted);
  }

  .tool-chevron {
    color: var(--muted);
    transition: transform 0.2s;
    margin-left: 2px;
    flex-shrink: 0;
  }
  .tool-block.expanded .tool-chevron { transform: rotate(180deg); }

  .tool-body {
    display: none;
    border-top: 1px solid var(--border);
    background: var(--surface);
    border-radius: 6px;
    margin-top: 4px;
    overflow: hidden;
  }
  .tool-block.expanded .tool-body { display: block; }

  .tool-section {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
  }
  .tool-section:last-child { border-bottom: none; }

  .tool-section-label {
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 6px;
    font-weight: 500;
    display: flex; align-items: center; justify-content: space-between;
  }

  .tool-copy-btn {
    background: transparent;
    border: 1px solid transparent;
    color: var(--muted);
    padding: 1px 6px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 11px;
    font-family: var(--font-mono);
    letter-spacing: 0.05em;
    transition: all 0.12s;
  }
  .tool-copy-btn:hover { color: var(--fg-dim); border-color: var(--border2); background: var(--surface); }

  .tool-code {
    font-size: 11px;
    line-height: 1.65;
    color: var(--fg-dim);
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 300px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  .tool-code.streaming {
    border-left: 2px solid var(--amber);
    padding-left: 10px;
  }
  .tool-code.streaming::after {
    content: '\25ae';
    animation: cursor-blink 0.75s step-end infinite;
  }
  @keyframes cursor-blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }

  .tool-result-ok { color: var(--fg-dim); }

  /* Header summary (file path, command preview) */
  .tool-summary {
    font-size: 11px;
    color: var(--muted);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  /* Error block */
  .tool-error-block {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px 12px;
    background: var(--red-soft);
    border: 1px solid color-mix(in srgb, var(--red) 30%, transparent);
    border-radius: 4px;
  }
  .tool-error-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--red);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    flex-shrink: 0;
  }
  .tool-error-msg {
    font-size: 11px;
    color: var(--red);
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
  }

  /* Read: file content */
  .tool-file-content {
    font-size: 11px;
    line-height: 1.65;
    color: var(--fg-dim);
    white-space: pre;
    overflow-x: auto;
    overflow-y: auto;
    max-height: 400px;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
    padding: 4px 0;
    tab-size: 4;
  }

  /* Bash: command echo */
  .tool-bash-cmd {
    font-size: 11px;
    line-height: 1.65;
    color: var(--fg-bright);
    white-space: pre-wrap;
    word-break: break-word;
    padding: 4px 0;
    font-weight: 600;
  }

  /* Bash: stderr */
  .tool-stderr {
    color: var(--amber);
  }

  /* Bash: exit code */
  .exit-ok { color: var(--green); font-weight: 600; }
  .exit-fail { color: var(--red); font-weight: 600; }

  /* Grep: match list */
  .tool-match-list {
    max-height: 350px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }
  .tool-match-row {
    display: flex;
    gap: 12px;
    padding: 3px 0;
    font-size: 11px;
    line-height: 1.5;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 40%, transparent);
  }
  .tool-match-row:last-child { border-bottom: none; }
  .tool-match-file {
    color: var(--accent-soft);
    flex-shrink: 0;
    min-width: 0;
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tool-match-line {
    color: var(--muted);
  }
  .tool-match-content {
    color: var(--fg-dim);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Glob: file list */
  .tool-file-list {
    max-height: 300px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }
  .tool-file-row {
    font-size: 11px;
    color: var(--fg-dim);
    padding: 2px 0;
    line-height: 1.5;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 30%, transparent);
  }
  .tool-file-row:last-child { border-bottom: none; }

  /* Edit: diff view */
  .tool-diff {
    border-radius: 4px;
    overflow: hidden;
    font-size: 11px;
    line-height: 1.65;
    border: 1px solid var(--border2);
  }
  .tool-diff-del {
    background: color-mix(in srgb, var(--red) 10%, var(--surface));
    color: var(--red);
    padding: 8px 12px;
    white-space: pre-wrap;
    word-break: break-word;
    border-bottom: 1px solid var(--border2);
  }
  .tool-diff-del::before {
    content: '- ';
    font-weight: 700;
    opacity: 0.6;
  }
  .tool-diff-add {
    background: color-mix(in srgb, var(--green) 10%, var(--surface));
    color: var(--green);
    padding: 8px 12px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .tool-diff-add::before {
    content: '+ ';
    font-weight: 700;
    opacity: 0.6;
  }

  /* Task: subagent prompt + response */
  .tool-task-prompt {
    font-size: 11px;
    line-height: 1.65;
    color: var(--fg-dim);
    white-space: pre-wrap;
    word-break: break-word;
    padding: 6px 10px;
    background: var(--surface2);
    border-left: 2px solid var(--accent-soft);
    border-radius: 3px;
    font-family: var(--font-ui);
    max-height: 160px;
    overflow-y: auto;
  }
  .tool-task-result {
    font-size: 12px;
    line-height: 1.65;
    color: var(--fg-bright);
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--font-ui);
    max-height: 360px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  /* Tool description (pulled from registry) */
  .tool-description {
    font-size: 12px;
    line-height: 1.55;
    color: var(--fg-dim);
    font-family: var(--font-ui);
  }

  /* Generic params key/value rows */
  .tool-params {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .tool-param-row {
    display: grid;
    grid-template-columns: minmax(90px, auto) 1fr;
    gap: 12px;
    font-size: 11px;
    line-height: 1.5;
    padding: 2px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 40%, transparent);
  }
  .tool-param-row:last-child { border-bottom: none; }
  .tool-param-key {
    color: var(--muted);
    font-weight: 500;
  }
  .tool-param-value {
    color: var(--fg-dim);
    overflow-wrap: anywhere;
    word-break: break-word;
  }

  /* Shared: metadata line */
  .tool-meta {
    font-size: 11px;
    color: var(--muted);
    padding: 6px 14px;
    letter-spacing: 0.03em;
  }

  /* Shared: empty state */
  .tool-empty {
    font-size: 11px;
    color: var(--muted);
    font-style: italic;
    padding: 4px 0;
  }

  /* Expand/collapse output */
  .output-full {
    max-height: none !important;
  }

  .tool-expand-btn {
    width: 100%;
    background: none;
    border: none;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    padding: 5px 0;
    cursor: pointer;
    transition: color 0.12s ease, background 0.12s ease;
  }
  .tool-expand-btn:hover {
    color: var(--fg-dim);
    background: var(--surface2);
  }

  .tool-images {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  @media (max-width: 768px) {
    .tool-header {
      padding: 8px 4px;
      min-height: 40px;
      flex-wrap: wrap;
      row-gap: 4px;
    }
    .tool-summary {
      white-space: normal;
      word-break: break-word;
      overflow: visible;
      text-overflow: clip;
      flex-basis: 100%;
      order: 99;
      padding-left: 14px;
      font-size: 11px;
      line-height: 1.4;
    }
    .tool-name, .tool-result-preview, .tool-duration {
      font-size: 12px;
    }
    .tool-expand-btn {
      padding: 10px 0;
      font-size: 11px;
      min-height: 40px;
    }
    .tool-copy-btn {
      padding: 6px 10px;
      font-size: 12px;
      min-height: 32px;
    }
    .tool-match-file {
      max-width: 140px;
    }
    .tool-param-row {
      grid-template-columns: minmax(70px, auto) 1fr;
      gap: 8px;
    }
  }
</style>
