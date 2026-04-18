<script>
  import ToolCall from './ToolCall.svelte';

  let { name, calls } = $props();
  let expanded = $state(false);

  let aggStatus = $derived.by(() => {
    if (calls.some(c => c.status === 'error')) return 'error';
    if (calls.some(c => c.status === 'running')) return 'running';
    return 'done';
  });

  let totalDuration = $derived.by(() => {
    const sum = calls.reduce((s, c) => s + (parseFloat(c.duration) || 0), 0);
    return sum >= 10 ? sum.toFixed(0) : sum.toFixed(1);
  });

  let errorCount = $derived(calls.filter(c => c.status === 'error').length);
  let runningCount = $derived(calls.filter(c => c.status === 'running').length);

  let previewText = $derived.by(() => {
    if (errorCount > 0) return `${errorCount} error${errorCount !== 1 ? 's' : ''}`;
    if (runningCount > 0) return `${runningCount} running`;
    return 'all done';
  });
</script>

<div class="tool-group" class:expanded role="region" aria-label="Grouped {name} calls">
  <button class="tg-header" onclick={() => expanded = !expanded} aria-expanded={expanded}>
    <div class="tg-dot" class:running={aggStatus === 'running'} class:done={aggStatus === 'done'} class:error={aggStatus === 'error'}></div>
    <span class="tg-name">{name}</span>
    <span class="tg-count">× {calls.length}</span>
    <span class="tg-preview" class:error={errorCount > 0}>{previewText}</span>
    {#if totalDuration > 0}
      <span class="tg-duration">{totalDuration}s</span>
    {/if}
    <svg class="tg-chevron" width="8" height="8" viewBox="0 0 10 10" fill="none" aria-hidden="true">
      <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </button>

  {#if expanded}
    <div class="tg-body">
      {#each calls as tc (tc.id)}
        <ToolCall {tc} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .tool-group {
    border-left: 2px solid var(--border2);
    padding-left: 8px;
    margin-left: -2px;
    transition: border-color 0.15s ease;
    font-family: var(--font-mono);
  }
  .tool-group:hover { border-left-color: var(--accent-soft); }
  .tool-group.expanded { border-left-color: var(--accent); }

  .tg-header {
    padding: 6px 4px;
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    width: 100%;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    font-family: var(--font-mono);
    text-align: left;
    min-height: 32px;
    transition: background 0.15s ease;
  }
  .tg-header:hover { background: var(--surface2); border-radius: 4px; }

  .tg-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .tg-dot.running {
    background: var(--amber);
    animation: tg-pulse 1.2s ease-in-out infinite;
  }
  .tg-dot.done { background: var(--green); }
  .tg-dot.error { background: var(--red); }
  @keyframes tg-pulse {
    0%, 100% { opacity: 0.3; }
    50% { opacity: 1; }
  }

  .tg-name {
    font-size: 12px;
    font-weight: 500;
    color: var(--fg-dim);
  }

  .tg-count {
    font-size: 12px;
    color: var(--accent-soft);
    font-weight: 600;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
  }

  .tg-preview {
    font-size: 11px;
    color: var(--muted);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .tg-preview.error { color: var(--red); }

  .tg-duration {
    font-size: 12px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
  }

  .tg-chevron {
    color: var(--muted);
    transition: transform 0.2s;
    margin-left: 2px;
    flex-shrink: 0;
  }
  .tool-group.expanded .tg-chevron { transform: rotate(180deg); }

  .tg-body {
    padding: 2px 0 2px 10px;
    border-left: 1px dashed var(--border2);
    margin-left: 2px;
    margin-top: 4px;
    animation: tg-expand 0.18s ease;
  }
  @keyframes tg-expand {
    from { opacity: 0; transform: translateY(-2px); }
    to { opacity: 1; transform: translateY(0); }
  }

  @media (max-width: 768px) {
    .tg-header {
      padding: 8px 4px;
      min-height: 40px;
      flex-wrap: wrap;
      row-gap: 4px;
    }
    .tg-name, .tg-count, .tg-preview, .tg-duration { font-size: 12px; }
    .tg-body {
      padding-left: 6px;
      margin-left: 0;
    }
  }
</style>
