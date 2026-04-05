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
    const serverList = await resp.json();

    // Build a set of server IDs already loaded locally
    const localByServerId = {};
    for (const c of Object.values(appState.conversations)) {
      if (c.serverId) localByServerId[c.serverId] = c;
    }

    // Merge server conversations into the local map
    for (const sc of serverList) {
      if (localByServerId[sc.id]) {
        // Already loaded -- just update title/timestamps if server is newer
        const local = localByServerId[sc.id];
        if (sc.title && sc.title !== local.title && local.titleGenerated) {
          local.title = sc.title;
        }
        local.updatedAt = new Date(sc.updated_at).getTime();
      } else {
        // Not loaded locally -- add as a stub (no messages until clicked)
        const localId = 'srv_' + sc.id;
        if (!appState.conversations[localId]) {
          appState.conversations[localId] = {
            id: localId,
            serverId: sc.id,
            title: sc.title || 'Conversation',
            messages: [],
            createdAt: new Date(sc.created_at).getTime(),
            updatedAt: new Date(sc.updated_at).getTime(),
            titleGenerated: true,
            pinned: false,
            totalTokens: 0,
            stub: true, // not yet loaded from server
          };
        }
      }
    }

    // Remove stubs for conversations deleted on server
    const serverIds = new Set(serverList.map(sc => sc.id));
    for (const [id, c] of Object.entries(appState.conversations)) {
      if (c.stub && !serverIds.has(c.serverId)) {
        delete appState.conversations[id];
      }
    }
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
    // Remove all local entries pointing to this server conversation
    for (const [id, c] of Object.entries(appState.conversations)) {
      if (c.serverId === serverId) {
        delete appState.conversations[id];
      }
    }
  } catch {
    // silently ignore
  }
}

export async function searchConversations(query) {
  if (!appState.authToken || !query.trim()) return [];
  try {
    const resp = await fetch(`/api/conversations/search?q=${encodeURIComponent(query)}`, {
      headers: { 'Authorization': `Bearer ${appState.authToken}` },
    });
    if (!resp.ok) return [];
    return await resp.json();
  } catch {
    return [];
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
