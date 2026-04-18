<script>
  import { onMount, onDestroy, tick } from 'svelte';
  import * as d3 from 'd3';

  let { spec, title = '' } = $props();

  let chartEl = $state(null);
  let modalChartEl = $state(null);
  let tooltipEl = $state(null);
  let expanded = $state(false);
  let error = $state(null);
  let resizeObs = null;
  let modalResizeObs = null;

  const PALETTE = [
    '#818cf8', '#38bdf8', '#10b981', '#f59e0b', '#ef4444',
    '#a78bfa', '#fb923c', '#e879f9', '#2dd4bf', '#f472b6',
    '#6366f1', '#facc15',
  ];

  function cssVar(name, fallback) {
    if (typeof window === 'undefined') return fallback;
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function render(container, s, opts = {}) {
    if (!container || !s) return;
    d3.select(container).selectAll('*').remove();

    const rect = container.getBoundingClientRect();
    const width = Math.max(320, rect.width || 600);
    const height = Math.max(260, opts.expanded ? rect.height : 360);
    const margin = { top: 14, right: 24, bottom: 38, left: 52 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;

    const fg = cssVar('--fg', '#e8e8f2');
    const fgDim = cssVar('--fg-dim', '#c4c4d4');
    const muted = cssVar('--muted', '#8888a0');
    const border2 = cssVar('--border2', '#28282e');
    const gridCol = cssVar('--border', '#1e1e26');

    const series = Array.isArray(s.series) ? s.series : [];
    if (!series.length) return;

    const allX = series.flatMap(se => se.data.map(d => d.x));
    const allY = series.flatMap(se => se.data.map(d => d.y));
    const xIsNumeric = allX.every(v => typeof v === 'number' && isFinite(v));

    const svg = d3.select(container)
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .attr('viewBox', `0 0 ${width} ${height}`);

    const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

    const xScale = xIsNumeric
      ? d3.scaleLinear().domain(d3.extent(allX)).nice().range([0, innerW])
      : d3.scalePoint().domain([...new Set(allX.map(String))]).range([0, innerW]).padding(0.5);

    const yExtent = d3.extent(allY);
    const yPad = (yExtent[1] - yExtent[0]) * 0.08 || 1;
    const yScale = d3.scaleLinear()
      .domain([yExtent[0] - yPad, yExtent[1] + yPad])
      .nice()
      .range([innerH, 0]);

    // Grid
    g.append('g')
      .attr('class', 'grid')
      .call(d3.axisLeft(yScale).tickSize(-innerW).tickFormat(() => ''))
      .call(gg => gg.select('.domain').remove())
      .selectAll('line')
      .attr('stroke', gridCol)
      .attr('stroke-opacity', 1);

    // Axes
    const xAxis = xIsNumeric
      ? d3.axisBottom(xScale).ticks(Math.min(8, Math.floor(innerW / 70)))
      : d3.axisBottom(xScale);

    g.append('g')
      .attr('class', 'x-axis')
      .attr('transform', `translate(0,${innerH})`)
      .call(xAxis)
      .call(ax => {
        ax.selectAll('text').attr('fill', muted).style('font-size', '10px');
        ax.selectAll('path,line').attr('stroke', border2);
      });

    g.append('g')
      .attr('class', 'y-axis')
      .call(d3.axisLeft(yScale).ticks(6))
      .call(ax => {
        ax.selectAll('text').attr('fill', muted).style('font-size', '10px');
        ax.selectAll('path,line').attr('stroke', border2);
      });

    // Axis labels
    if (s.xLabel) {
      svg.append('text')
        .attr('x', margin.left + innerW / 2)
        .attr('y', height - 6)
        .attr('text-anchor', 'middle')
        .attr('fill', fgDim)
        .style('font-size', '11px')
        .text(s.xLabel);
    }
    if (s.yLabel) {
      svg.append('text')
        .attr('transform', `translate(14,${margin.top + innerH / 2}) rotate(-90)`)
        .attr('text-anchor', 'middle')
        .attr('fill', fgDim)
        .style('font-size', '11px')
        .text(s.yLabel);
    }

    const line = d3.line()
      .x(d => xIsNumeric ? xScale(d.x) : xScale(String(d.x)))
      .y(d => yScale(d.y))
      .curve(d3.curveMonotoneX);

    // Draw each series
    series.forEach((se, i) => {
      const color = PALETTE[i % PALETTE.length];
      g.append('path')
        .datum(se.data)
        .attr('fill', 'none')
        .attr('stroke', color)
        .attr('stroke-width', 1.8)
        .attr('stroke-linejoin', 'round')
        .attr('stroke-linecap', 'round')
        .attr('d', line);
    });

    // Legend (only if >1 series)
    if (series.length > 1) {
      const legend = svg.append('g')
        .attr('class', 'legend')
        .attr('transform', `translate(${margin.left},${margin.top - 4})`);
      let xOff = 0;
      series.forEach((se, i) => {
        const color = PALETTE[i % PALETTE.length];
        const gi = legend.append('g').attr('transform', `translate(${xOff},0)`);
        gi.append('rect').attr('width', 10).attr('height', 2).attr('y', 6).attr('fill', color);
        const t = gi.append('text').attr('x', 14).attr('y', 9).attr('fill', muted)
          .style('font-size', '10px').text(se.name || `Series ${i + 1}`);
        xOff += 14 + (t.node().getComputedTextLength?.() || 40) + 16;
      });
    }

    // Hover interaction
    const hoverDot = g.append('circle')
      .attr('r', 3.5)
      .attr('fill', cssVar('--bg', '#0c0c0e'))
      .attr('stroke-width', 2)
      .style('display', 'none');

    const hoverLine = g.append('line')
      .attr('y1', 0).attr('y2', innerH)
      .attr('stroke', border2)
      .attr('stroke-dasharray', '2 3')
      .style('display', 'none');

    const overlay = g.append('rect')
      .attr('width', innerW)
      .attr('height', innerH)
      .attr('fill', 'transparent')
      .style('cursor', 'crosshair');

    overlay.on('mousemove', (ev) => {
      if (!xIsNumeric) return;
      const [mx] = d3.pointer(ev, g.node());
      const xVal = xScale.invert(mx);
      let best = null;
      let bestDist = Infinity;
      let bestColor = PALETTE[0];
      series.forEach((se, i) => {
        se.data.forEach(d => {
          const dist = Math.abs(d.x - xVal);
          if (dist < bestDist) { bestDist = dist; best = { ...d, series: se.name }; bestColor = PALETTE[i % PALETTE.length]; }
        });
      });
      if (!best) return;
      const px = xScale(best.x);
      const py = yScale(best.y);
      hoverDot
        .attr('cx', px).attr('cy', py)
        .attr('stroke', bestColor)
        .style('display', null);
      hoverLine.attr('x1', px).attr('x2', px).style('display', null);
      if (tooltipEl) {
        const bodyRect = container.getBoundingClientRect();
        tooltipEl.style.display = 'block';
        tooltipEl.style.left = `${margin.left + px + 12}px`;
        tooltipEl.style.top = `${margin.top + py - 10}px`;
        const xFmt = typeof best.x === 'number' ? (+best.x.toFixed(4)) : best.x;
        const yFmt = typeof best.y === 'number' ? (+best.y.toFixed(4)) : best.y;
        tooltipEl.innerHTML = `
          ${series.length > 1 ? `<div style="color:${bestColor};font-weight:600">${best.series || ''}</div>` : ''}
          <div>${s.xLabel || 'x'}: <b>${xFmt}</b></div>
          <div>${s.yLabel || 'y'}: <b>${yFmt}</b></div>`;
      }
    });

    overlay.on('mouseleave', () => {
      hoverDot.style('display', 'none');
      hoverLine.style('display', 'none');
      if (tooltipEl) tooltipEl.style.display = 'none';
    });
  }

  onMount(() => {
    try {
      render(chartEl, spec);
      if (chartEl && typeof ResizeObserver !== 'undefined') {
        resizeObs = new ResizeObserver(() => render(chartEl, spec));
        resizeObs.observe(chartEl);
      }
    } catch (e) {
      error = e.message;
    }
  });

  onDestroy(() => {
    if (resizeObs) resizeObs.disconnect();
    if (modalResizeObs) modalResizeObs.disconnect();
  });

  async function openExpanded() {
    expanded = true;
    await tick();
    try {
      render(modalChartEl, spec, { expanded: true });
      if (modalChartEl && typeof ResizeObserver !== 'undefined') {
        modalResizeObs = new ResizeObserver(() => render(modalChartEl, spec, { expanded: true }));
        modalResizeObs.observe(modalChartEl);
      }
    } catch (e) {
      error = e.message;
    }
  }

  function closeExpanded() {
    if (modalResizeObs) { modalResizeObs.disconnect(); modalResizeObs = null; }
    expanded = false;
  }

  function onKeydown(e) {
    if (e.key === 'Escape' && expanded) closeExpanded();
  }
</script>

<svelte:window on:keydown={expanded ? onKeydown : null} />

<div class="chart-container">
  <div class="chart-header">
    <span class="chart-title">{title || spec?.title || 'Chart'}</span>
    <button class="expand-btn" onclick={openExpanded} title="Expand chart" aria-label="Expand chart">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 3 21 3 21 9"></polyline>
        <polyline points="9 21 3 21 3 15"></polyline>
        <line x1="21" y1="3" x2="14" y2="10"></line>
        <line x1="3" y1="21" x2="10" y2="14"></line>
      </svg>
    </button>
  </div>
  {#if error}
    <div class="chart-error">Chart error: {error}</div>
  {/if}
  <div class="chart-el" bind:this={chartEl}></div>
  <div class="tooltip" bind:this={tooltipEl}></div>
</div>

{#if expanded}
  <div class="chart-modal-backdrop" onclick={closeExpanded} role="presentation">
    <div class="chart-modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
      <div class="chart-modal-header">
        <span class="chart-title">{title || spec?.title || 'Chart'}</span>
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
    position: relative;
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

  .chart-error {
    padding: 20px;
    text-align: center;
    font-size: 11px;
    font-family: var(--font-mono, 'Geist Mono', monospace);
    color: var(--red);
  }

  .tooltip {
    position: absolute;
    display: none;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 11px;
    color: var(--fg-bright);
    pointer-events: none;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    z-index: 5;
    white-space: nowrap;
  }

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
