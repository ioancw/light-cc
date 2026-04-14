<script>
  import { onMount, onDestroy, tick } from 'svelte';
  import { renderChart } from '../../lib/plotly.js';

  let { plotlyJson, title = '' } = $props();
  let chartEl = $state(null);
  let modalChartEl = $state(null);
  let loading = $state(true);
  let error = $state(null);
  let expanded = $state(false);

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
    if (modalChartEl && window.Plotly) {
      try { window.Plotly.purge(modalChartEl); } catch {}
    }
  });

  async function openExpanded() {
    expanded = true;
    await tick();
    if (modalChartEl && plotlyJson) {
      try { await renderChart(modalChartEl, plotlyJson, { expanded: true }); } catch {}
    }
  }

  function closeExpanded() {
    if (modalChartEl && window.Plotly) {
      try { window.Plotly.purge(modalChartEl); } catch {}
    }
    expanded = false;
  }

  function onKeydown(e) {
    if (e.key === 'Escape' && expanded) closeExpanded();
  }
</script>

<svelte:window on:keydown={expanded ? onKeydown : null} />

<div class="chart-container">
  <div class="chart-header">
    <span class="chart-title">{title || 'Chart'}</span>
    <button class="expand-btn" onclick={openExpanded} title="Expand chart" aria-label="Expand chart">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 3 21 3 21 9"></polyline>
        <polyline points="9 21 3 21 3 15"></polyline>
        <line x1="21" y1="3" x2="14" y2="10"></line>
        <line x1="3" y1="21" x2="10" y2="14"></line>
      </svg>
    </button>
  </div>
  {#if loading && !error}
    <div class="chart-loading">Loading chart...</div>
  {/if}
  {#if error}
    <div class="chart-error">Chart error: {error}</div>
  {/if}
  <div class="chart-el" bind:this={chartEl}></div>
</div>

{#if expanded}
  <div class="chart-modal-backdrop" onclick={closeExpanded} role="presentation">
    <div class="chart-modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
      <div class="chart-modal-header">
        <span class="chart-title">{title || 'Chart'}</span>
        <button class="expand-btn" onclick={closeExpanded} title="Close" aria-label="Close expanded chart">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
      <div class="chart-modal-body" bind:this={modalChartEl}></div>
    </div>
  </div>
{/if}

<style>
  .chart-container {
    border: 1px solid var(--border2);
    border-radius: 6px;
    overflow: hidden;
    background: var(--surface);
  }

  .chart-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 10px 4px 14px;
  }

  .chart-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: 0.03em;
  }

  .expand-btn {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px;
    color: var(--fg-dim);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: background 0.12s var(--transition), color 0.12s var(--transition), border-color 0.12s var(--transition);
  }
  .expand-btn:hover {
    background: var(--surface2);
    color: var(--fg-bright);
    border-color: var(--border2);
  }

  .chart-el {
    width: 100%;
    min-height: 360px;
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

  .chart-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 32px;
  }

  .chart-modal {
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: 8px;
    width: min(1400px, 96vw);
    height: min(900px, 92vh);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
  }

  .chart-modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }

  .chart-modal-body {
    flex: 1;
    min-height: 0;
    width: 100%;
  }
</style>
