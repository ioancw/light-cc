// Lazy-load Plotly for chart rendering.

let _plotlyPromise = null;

export const CHART_THEME = {
  colorway: [
    '#818cf8', '#38bdf8', '#10b981', '#f59e0b', '#ef4444',
    '#a78bfa', '#fb923c', '#e879f9', '#2dd4bf', '#f472b6',
    '#6366f1', '#facc15',
  ],
  font: { family: 'Geist Mono, monospace', size: 11, color: '#c4c4d4' },
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  margin: { l: 48, r: 16, t: 44, b: 40 },
  title: { font: { size: 14, color: '#e8e8f2' }, x: 0, xanchor: 'left' },
  xaxis: {
    gridcolor: '#1e1e26', zerolinecolor: '#28282e', linecolor: '#28282e',
    tickfont: { size: 10, color: '#8888a0' }, title: { font: { size: 11, color: '#8888a0' } },
  },
  yaxis: {
    gridcolor: '#1e1e26', zerolinecolor: '#28282e', linecolor: '#28282e',
    tickfont: { size: 10, color: '#8888a0' }, title: { font: { size: 11, color: '#8888a0' } },
  },
  legend: { bgcolor: 'rgba(0,0,0,0)', borderwidth: 0, font: { size: 10, color: '#8888a0' } },
  hoverlabel: {
    bgcolor: '#16161c', bordercolor: '#28282e',
    font: { family: 'Geist Mono, monospace', size: 11, color: '#e8e8f2' },
  },
};

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

export async function renderChart(el, plotlyJson) {
  if (!el || !plotlyJson) return;
  try {
    const Plotly = await loadPlotly();
    const fig = typeof plotlyJson === 'string' ? JSON.parse(plotlyJson) : plotlyJson;
    const layout = deepMerge(deepMerge({}, CHART_THEME), fig.layout || {});
    layout.paper_bgcolor = CHART_THEME.paper_bgcolor;
    layout.plot_bgcolor = CHART_THEME.plot_bgcolor;
    layout.font = CHART_THEME.font;
    layout.hoverlabel = CHART_THEME.hoverlabel;
    layout.autosize = true;
    delete layout.width;
    delete layout.height;

    Plotly.newPlot(el, fig.data, layout, { responsive: true, displayModeBar: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] });
  } catch (e) {
    console.error('Plotly render error:', e);
  }
}
