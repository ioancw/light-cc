// Theme management: read, write, and apply themes.

import { appState } from './state.svelte.js';

export const THEMES = [
  { name: 'midnight', label: 'Midnight', color: '#0c0c0e' },
  { name: 'light', label: 'Light', color: '#f5f5f7' },
  { name: 'dracula', label: 'Dracula', color: '#bd93f9' },
  { name: 'solarized', label: 'Solarized', color: '#268bd2' },
  { name: 'nord', label: 'Nord', color: '#88c0d0' },
];

export function setTheme(name) {
  appState.theme = name;
  if (name === 'midnight') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.setAttribute('data-theme', name);
  }
  localStorage.setItem('lcc_theme', name);
}

export function restoreTheme() {
  const saved = localStorage.getItem('lcc_theme');
  if (saved) {
    setTheme(saved);
  } else {
    // Auto-detect OS preference
    const preferLight = window.matchMedia('(prefers-color-scheme: light)').matches;
    setTheme(preferLight ? 'light' : 'midnight');
  }
}
