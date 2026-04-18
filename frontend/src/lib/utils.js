// Shared utility functions.

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  // Same day: just show time. Different day: show date + time.
  if (d.toDateString() === now.toDateString()) return time;
  const date = d.toLocaleDateString([], { day: 'numeric', month: 'short' });
  return `${date}, ${time}`;
}

export function formatTokens(n) {
  return (n || 0).toLocaleString();
}

export function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

export function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function generateId(prefix = 'id') {
  return prefix + '_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

// Friendly model label: "claude-sonnet-4-5-latest" -> "Sonnet 4.5"
export function modelLabel(modelId) {
  const parts = modelId.split('-');
  if (parts[0] === 'claude' && parts.length >= 3) {
    const family = parts[1].charAt(0).toUpperCase() + parts[1].slice(1);
    const ver = parts.slice(2).filter(p => !/^\d{8}$/.test(p) && p !== 'latest').join('.');
    return family + (ver ? ' ' + ver : '');
  }
  return modelId;
}

// Compact label for tight mobile headers: "claude-sonnet-4-5-latest" -> "S 4.5"
export function modelShortLabel(modelId) {
  const parts = modelId.split('-');
  if (parts[0] === 'claude' && parts.length >= 3) {
    const fam = parts[1].charAt(0).toUpperCase();
    const ver = parts.slice(2).filter(p => !/^\d{8}$/.test(p) && p !== 'latest').join('.');
    return fam + (ver ? ' ' + ver : '');
  }
  return modelId.slice(0, 6);
}
