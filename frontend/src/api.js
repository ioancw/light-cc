// REST API client for auth, conversations, and files.

import { appState, setAuth, clearAuth } from './state.svelte.js';

function authHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  if (appState.authToken) {
    headers['Authorization'] = `Bearer ${appState.authToken}`;
  }
  return headers;
}

// ── Auth ──

export async function login(email, password) {
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || 'Login failed');
  setAuth(data);
  return data;
}

export async function register(displayName, email, password) {
  const resp = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: displayName, email, password }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || 'Registration failed');
  setAuth(data);
  return data;
}

export function logout() {
  clearAuth();
  window.location.href = '/login';
}

// ── Conversations ──

export async function fetchConversationHistory(query) {
  if (!appState.authToken) return;
  try {
    let url = '/api/conversations';
    if (query) url += `?q=${encodeURIComponent(query)}`;
    const resp = await fetch(url, {
      headers: { 'Authorization': `Bearer ${appState.authToken}` },
    });
    if (!resp.ok) return;
    appState.serverConversations = await resp.json();
  } catch {
    // silently ignore
  }
}

export async function renameConversation(serverId, title) {
  if (!appState.authToken || !serverId) return;
  try {
    await fetch(`/api/conversations/${serverId}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ title }),
    });
  } catch {
    // silently ignore
  }
}

export async function deleteServerConversation(serverId) {
  if (!appState.authToken) return;
  try {
    await fetch(`/api/conversations/${serverId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${appState.authToken}` },
    });
    appState.serverConversations = appState.serverConversations.filter(c => c.id !== serverId);
  } catch {
    // silently ignore
  }
}

export async function importConversation(file) {
  const formData = new FormData();
  formData.append('file', file);
  const resp = await fetch('/api/conversations/import', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${appState.authToken}` },
    body: formData,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Import failed');
  }
  return resp.json();
}

// ── Files ──

export async function listFiles(path = '') {
  const resp = await fetch(`/api/files/list?path=${encodeURIComponent(path)}`, {
    headers: { 'Authorization': `Bearer ${appState.authToken}` },
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

export async function readFile(path) {
  const resp = await fetch(`/api/files/read?path=${encodeURIComponent(path)}`, {
    headers: { 'Authorization': `Bearer ${appState.authToken}` },
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

export async function uploadFile(path, file) {
  const formData = new FormData();
  formData.append('file', file);
  const resp = await fetch(`/api/files/upload?path=${encodeURIComponent(path)}`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${appState.authToken}` },
    body: formData,
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}
