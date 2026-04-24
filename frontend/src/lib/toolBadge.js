// Maps a tool name to its visual identity: category key, accent color
// (CSS variable name), label text, and an SVG glyph path drawn inside a
// 12x12 viewBox at 1.3px stroke width.
//
// ToolCall and ToolGroup both consume this so grouped + individual views
// share the same color language.

/**
 * @typedef {Object} ToolBadge
 * @property {string} cls    category key (bash|python|read|write|search|task|chart|generic)
 * @property {string} color  CSS custom property reference, e.g. `var(--green)`
 * @property {string} label  short category label
 * @property {string} glyph  SVG path `d` attribute, rendered at 12x12
 */

const GLYPHS = {
  // Terminal prompt: `>`
  bash:    'M2 3l2.5 3L2 9 M6 9h4',
  // Angle brackets: `</>`
  python:  'M4 3L1 6l3 3 M8 3l3 3-3 3',
  // Document with lines
  read:    'M3 2h5l2 2v6H3V2z M5 5h3 M5 7h3 M5 9h2',
  // Pencil
  write:   'M2 10l2-.5 6-6-1.5-1.5-6 6L2 10z',
  // Magnifier
  search:  'M7 7l3 3 M5 3a3 3 0 110 6 3 3 0 010-6z',
  // Two nested rounded rects + connecting stroke
  task:    'M1.5 1.5h4v4h-4z M6.5 6.5h4v4h-4z M5.5 5v1.5h1',
  // Bar chart
  chart:   'M2 10h9 M3 10V6 M5 10V4 M7 10V7 M9 10V2',
  // Filled dot
  generic: 'M6 4.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3z',
};

/**
 * @param {string} name tool name from the backend
 * @returns {ToolBadge}
 */
export function getToolBadge(name) {
  const n = (name || '').toLowerCase();
  if (n === 'bash') return badge('bash', 'var(--amber)', 'bash');
  if (n === 'python_exec' || n === 'python') return badge('python', 'var(--blue)', 'python');
  if (n.includes('chart')) return badge('chart', 'var(--accent2, var(--accent))', 'chart');
  if (n.includes('read') || n.includes('fetch') || n.includes('get')) return badge('read', 'var(--green)', 'read');
  if (n.includes('write') || n.includes('create') || n.includes('save')) return badge('write', 'var(--accent)', 'write');
  if (n.includes('edit')) return badge('write', 'var(--accent)', 'edit');
  if (n.includes('search') || n.includes('find') || n.includes('query') || n.includes('grep') || n.includes('glob')) return badge('search', 'var(--blue)', 'search');
  if (n === 'task' || n === 'agent' || n.startsWith('task_')) return badge('task', 'var(--accent-soft)', 'task');
  return badge('generic', 'var(--muted)', 'tool');
}

function badge(cls, color, label) {
  return { cls, color, label, glyph: GLYPHS[cls] || GLYPHS.generic };
}
