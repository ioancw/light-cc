<script>
  import { onMount, onDestroy } from 'svelte';
  import { renderChart } from '../../lib/plotly.js';

  let { plotlyJson, title = '' } = $props();
  let chartEl = $state(null);
  let loading = $state(true);
  let error = $state(null);

  onMount(async () => {
    if (!chartEl || !plotlyJson) return;
    try {
      await renderChart(chartEl, plotlyJson);
      loading = false;
    } catch (e) {
      error = e.message;
      loading = false;
    }
  });

  onDestroy(() => {
    if (chartEl && window.Plotly) {
      try { window.Plotly.purge(chartEl); } catch {}
    }
  });
</script>

<div class="chart-container">
  {#if title}
    <div class="chart-title">{title}</div>
  {/if}
  {#if loading && !error}
    <div class="chart-loading">Loading chart...</div>
  {/if}
  {#if error}
    <div class="chart-error">Chart error: {error}</div>
  {/if}
  <div class="chart-el" bind:this={chartEl}></div>
</div>

<style>
  .chart-container {
    border: 1px solid var(--border2);
    border-radius: 6px;
    overflow: hidden;
    background: var(--surface);
  }

  .chart-title {
    padding: 8px 14px 0;
    font-size: 12px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: 0.03em;
  }

  .chart-el {
    width: 100%;
    min-height: 300px;
  }

  .chart-loading, .chart-error {
    padding: 20px;
    text-align: center;
    font-size: 11px;
    font-family: 'Geist Mono', monospace;
    letter-spacing: 0.04em;
  }
  .chart-loading { color: var(--muted); }
  .chart-error { color: var(--red); }
</style>
