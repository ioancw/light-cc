<script>
  let { html } = $props();
</script>

<div class="table-wrapper">
  {@html html}
</div>

<style>
  .table-wrapper {
    overflow-x: auto;
    border: 1px solid var(--border2);
    border-radius: 6px;
    background: var(--surface);
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  .table-wrapper :global(table) {
    width: 100%;
    border-collapse: collapse;
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
  }

  .table-wrapper :global(th) {
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
    color: var(--fg-bright);
    background: var(--surface2);
    border-bottom: 1px solid var(--border2);
    letter-spacing: 0.04em;
    white-space: nowrap;
  }

  .table-wrapper :global(td) {
    padding: 6px 12px;
    color: var(--fg-dim);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }

  .table-wrapper :global(tr:last-child td) {
    border-bottom: none;
  }

  .table-wrapper :global(tr:hover td) {
    background: var(--surface2);
  }

  .table-wrapper :global(.num) {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
</style>
