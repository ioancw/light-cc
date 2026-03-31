<script>
  import Chart from './renderers/Chart.svelte';
  import Image from './renderers/Image.svelte';
  import Table from './renderers/Table.svelte';
  import HtmlEmbed from './renderers/HtmlEmbed.svelte';

  let { tc } = $props();
  let expanded = $state(false);

  function toggle() {
    expanded = !expanded;
  }

  function getToolBadge(name) {
    const n = name.toLowerCase();
    if (n === 'bash') return { cls: 'bash', label: 'bash' };
    if (n === 'python_exec') return { cls: 'python', label: 'python' };
    if (n.includes('chart')) return { cls: 'chart', label: 'chart' };
    if (n.includes('read') || n.includes('fetch') || n.includes('get')) return { cls: 'read', label: 'read' };
    if (n.includes('write') || n.includes('create') || n.includes('save') || n.includes('edit')) return { cls: 'write', label: 'write' };
    if (n.includes('search') || n.includes('find') || n.includes('query') || n.includes('grep') || n.includes('glob')) return { cls: 'search', label: 'search' };
    return { cls: 'generic', label: 'tool' };
  }

  let badge = $derived(getToolBadge(tc.name));

  let inputStr = $derived(
    typeof tc.input === 'object' && tc.input ? JSON.stringify(tc.input, null, 2) : ''
  );

  let resultText = $derived(
    tc.result
      ? (typeof tc.result === 'string' ? tc.result : JSON.stringify(tc.result, null, 2))
      : ''
  );

  let streamText = $derived(tc.streamBuffer || '');

  function copyText(text) {
    navigator.clipboard.writeText(text);
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="tool-block" class:expanded>
  <div class="tool-header" onclick={toggle}>
    <div class="tool-status-icon" class:running={tc.status === 'running'} class:done={tc.status === 'done'} class:error={tc.status === 'error'}>
      {#if tc.status === 'running'}&#8635;{:else if tc.status === 'error'}&#10005;{:else}&#10003;{/if}
    </div>
    <span class="tool-name">{tc.name}</span>
    <span class="tool-type-badge {badge.cls}">{badge.label}</span>
    {#if tc.duration}
      <span class="tool-duration">{tc.duration}s</span>
    {/if}
    <span class="tool-chevron">&#9660;</span>
  </div>

  <div class="tool-body">
    {#if inputStr}
      <div class="tool-section">
        <div class="tool-section-label">Input</div>
        <div class="tool-code">{inputStr}</div>
      </div>
    {/if}

    {#if streamText && tc.status === 'running'}
      <div class="tool-section">
        <div class="tool-section-label">
          <span>Live Output</span>
        </div>
        <div class="tool-code streaming">{streamText}</div>
      </div>
    {/if}

    {#if tc.result}
      <div class="tool-section">
        <div class="tool-section-label">
          <span>Output</span>
          <button class="tool-copy-btn" onclick={(e) => { e.stopPropagation(); copyText(resultText); }}>copy</button>
        </div>
        <div class="tool-code" class:tool-result-err={tc.status === 'error'} class:tool-result-ok={tc.status !== 'error'}>{resultText}</div>
      </div>
    {/if}

    {#if tc.images && tc.images.length > 0}
      <div class="tool-section">
        <div class="tool-section-label">Images</div>
        <div class="tool-images">
          {#each tc.images as img}
            <Image src={img.data} alt={img.name || 'output'} mime={img.mime} />
          {/each}
        </div>
      </div>
    {/if}

    {#if tc.tables && tc.tables.length > 0}
      <div class="tool-section">
        <div class="tool-section-label">Tables</div>
        {#each tc.tables as html}
          <Table {html} />
        {/each}
      </div>
    {/if}

    {#if tc.chart}
      <div class="tool-section">
        <Chart plotlyJson={tc.chart.plotlyJson} title={tc.chart.title} />
      </div>
    {/if}

    {#if tc.embeds && tc.embeds.length > 0}
      <div class="tool-section">
        {#each tc.embeds as embed}
          <HtmlEmbed html={embed.html} name={embed.name} />
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .tool-block {
    border: 1px solid var(--border2);
    border-radius: 5px;
    overflow: hidden;
    background: var(--surface);
    font-family: 'Geist Mono', monospace;
  }

  .tool-header {
    padding: 8px 14px;
    display: flex;
    align-items: center;
    gap: 10px;
    cursor: pointer;
    transition: background 0.12s;
  }
  .tool-header:hover { background: var(--surface2); }

  .tool-status-icon {
    width: 16px; height: 16px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px;
    flex-shrink: 0;
  }
  .tool-status-icon.running {
    background: var(--amber-soft);
    border: 1px solid var(--amber);
    animation: spin-slow 1.5s linear infinite;
  }
  .tool-status-icon.done {
    background: var(--green-soft);
    border: 1px solid var(--green);
    color: var(--green);
  }
  .tool-status-icon.error {
    background: var(--red-soft);
    border: 1px solid var(--red);
    color: var(--red);
  }
  @keyframes spin-slow {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

  .tool-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: 0.04em;
  }

  .tool-type-badge {
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 3px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    font-weight: 500;
  }
  .tool-type-badge.bash { background: var(--amber-soft); color: var(--amber); border: 1px solid rgba(245,158,11,0.3); }
  .tool-type-badge.python { background: var(--blue-soft); color: var(--blue); border: 1px solid rgba(56,189,248,0.3); }
  .tool-type-badge.read { background: var(--blue-soft); color: var(--blue); border: 1px solid rgba(56,189,248,0.3); }
  .tool-type-badge.write { background: var(--green-soft); color: var(--green); border: 1px solid rgba(16,185,129,0.3); }
  .tool-type-badge.chart { background: var(--accent-glow); color: var(--accent-soft); border: 1px solid rgba(99,102,241,0.3); }
  .tool-type-badge.search { background: var(--accent-glow); color: var(--accent-soft); border: 1px solid rgba(99,102,241,0.3); }
  .tool-type-badge.generic { background: var(--surface2); color: var(--dim); border: 1px solid var(--border2); }

  .tool-duration {
    margin-left: auto;
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.05em;
  }

  .tool-chevron {
    font-size: 11px;
    color: var(--muted);
    transition: transform 0.2s;
    margin-left: 4px;
  }
  .tool-block.expanded .tool-chevron { transform: rotate(180deg); }

  .tool-body {
    display: none;
    border-top: 1px solid var(--border);
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
    font-family: 'Geist Mono', monospace;
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

  .tool-result-err { color: var(--red); }
  .tool-result-ok { color: var(--fg-dim); }

  .tool-images {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
