// Lazy-load Plotly for chart rendering.

let _plotlyPromise = null;

function readVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

function buildTheme() {
  const fg = readVar('--fg', '#c4c4d4');
  const fgBright = readVar('--fg-bright', '#e8e8f2');
  const fgDim = readVar('--fg-dim', '#8888a0');
  const border = readVar('--border', '#1e1e26');
  const border2 = readVar('--border2', '#28282e');
  const surface2 = readVar('--surface2', '#16161c');
  return {
    colorway: [
      '#818cf8', '#38bdf8', '#10b981', '#f59e0b', '#ef4444',
      '#a78bfa', '#fb923c', '#e879f9', '#2dd4bf', '#f472b6',
      '#6366f1', '#facc15',
    ],
    font: { family: 'Geist Mono, monospace', size: 11, color: fg },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    margin: { l: 56, r: 24, t: 16, b: 44 },
    xaxis: {
      gridcolor: border, zerolinecolor: border2, linecolor: border2,
      tickfont: { size: 10, color: fgDim }, title: { font: { size: 11, color: fgDim } },
    },
    yaxis: {
      gridcolor: border, zerolinecolor: border2, linecolor: border2,
      tickfont: { size: 10, color: fgDim }, title: { font: { size: 11, color: fgDim } },
    },
    legend: { bgcolor: 'rgba(0,0,0,0)', borderwidth: 0, font: { size: 10, color: fgDim } },
    hoverlabel: {
      bgcolor: surface2, bordercolor: border2,
      font: { family: 'Geist Mono, monospace', size: 11, color: fgBright },
    },
  };
}

export async function loadPlotly() {
  if (window.Plotly) return window.Plotly;
  if (_plotlyPromise) return _plotlyPromise;
  _plotlyPromise = import('plotly.js-dist-min').then(mod => {
    window.Plotly = mod.default || mod;
    return window.Plotly;
  });
  return _plotlyPromise;
}

function deepMerge(target, source) {
  for (const key of Object.keys(source)) {
    if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])
        && target[key] && typeof target[key] === 'object' && !Array.isArray(target[key])) {
      deepMerge(target[key], source[key]);
    } else {
      target[key] = source[key];
    }
  }
  return target;
}

function countSubplots(layout) {
  let xs = 0, ys = 0;
  for (const k of Object.keys(layout)) {
    if (/^xaxis\d*$/.test(k)) xs++;
    if (/^yaxis\d*$/.test(k)) ys++;
  }
  return Math.max(1, Math.max(xs, ys));
}

function applyAxisTheme(layout, theme) {
  for (const k of Object.keys(layout)) {
    if (!/^(xaxis|yaxis)\d*$/.test(k)) continue;
    const base = k.startsWith('x') ? theme.xaxis : theme.yaxis;
    const existing = layout[k] || {};
    layout[k] = {
      ...base,
      ...existing,
      tickfont: { ...base.tickfont, ...(existing.tickfont || {}) },
      title: {
        ...base.title,
        ...(typeof existing.title === 'object' ? existing.title : existing.title ? { text: existing.title } : {}),
        font: { ...base.title.font, ...(existing.title?.font || {}) },
      },
    };
  }
}

export async function renderChart(el, plotlyJson, opts = {}) {
  if (!el || !plotlyJson) return;
  try {
    const Plotly = await loadPlotly();
    const fig = typeof plotlyJson === 'string' ? JSON.parse(plotlyJson) : plotlyJson;
    const theme = buildTheme();
    const layout = deepMerge(deepMerge({}, theme), fig.layout || {});

    // Strip any baked-in template (plotly_dark etc.) so our theme wins.
    delete layout.template;
    // The Svelte chart-header already shows the title -- avoid duplicating it inside the figure.
    delete layout.title;
    layout.margin = { ...theme.margin, ...(fig.layout?.margin || {}), t: theme.margin.t };

    layout.paper_bgcolor = theme.paper_bgcolor;
    layout.plot_bgcolor = theme.plot_bgcolor;
    layout.font = { ...theme.font, ...(layout.font || {}) };
    // Force our color even if fig set it
    layout.font.color = theme.font.color;
    layout.hoverlabel = theme.hoverlabel;
    applyAxisTheme(layout, theme);
    layout.autosize = true;
    delete layout.width;
    delete layout.height;

    // Bump line traces to a slightly chunkier stroke for better contrast.
    const data = Array.isArray(fig.data) ? fig.data.map(tr => {
      if (tr && (tr.type === 'scatter' || tr.type === 'scattergl' || !tr.type) && tr.mode && tr.mode.includes('lines')) {
        return { ...tr, line: { width: 2.2, ...(tr.line || {}) } };
      }
      if (tr && (tr.type === 'scatter' || !tr.type) && !tr.mode) {
        // Default scatter mode includes lines
        return { ...tr, line: { width: 2.2, ...(tr.line || {}) } };
      }
      return tr;
    }) : fig.data;

    // In expanded mode the container already has explicit dimensions
    // (modal body fills its parent). Inline mode computes a height that
    // scales with subplot + annotation count.
    if (!opts.expanded) {
      const figHeight = fig.layout?.height;
      if (figHeight && figHeight > 0) {
        el.style.height = figHeight + 'px';
      } else {
        const n = countSubplots(layout);
        const anns = Array.isArray(layout.annotations) ? layout.annotations.length : 0;
        let h;
        if (n >= 4 || anns >= 6) h = 760;
        else if (n >= 2 || anns >= 3) h = 520;
        else h = 380;
        el.style.height = h + 'px';
      }
    }

    Plotly.newPlot(el, data, layout, {
      responsive: true,
      displayModeBar: opts.expanded ? 'hover' : false,
      displaylogo: false,
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
    });
  } catch (e) {
    console.error('Plotly render error:', e);
  }
}
