// REST API client for auth, conversations, and files.

import { appState, setAuth, clearAuth, showToast } from './state.svelte.js';

function logApiError(context, err) {
  // Background paths call this so a dropped network doesn't become a toast storm.
  // User-initiated calls add their own toast on top.
  console.error(`[api] ${context}:`, err);
}

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
    if (!resp.ok) {
      logApiError('fetchConversationHistory', `HTTP ${resp.status}`);
      return;
    }
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
        local.generating = !!sc.generating;
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
            generating: !!sc.generating,
          };
        } else {
          appState.conversations[localId].generating = !!sc.generating;
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
  } catch (err) {
    logApiError('fetchConversationHistory', err);
  }
}

export async function renameConversation(serverId, title) {
  if (!appState.authToken || !serverId) return;
  try {
    const resp = await fetch(`/api/conversations/${serverId}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ title }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  } catch (err) {
    logApiError('renameConversation', err);
    showToast('Rename failed', 'error');
  }
}

export async function deleteServerConversation(serverId) {
  if (!appState.authToken) return;
  try {
    const resp = await fetch(`/api/conversations/${serverId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${appState.authToken}` },
    });
    if (!resp.ok && resp.status !== 204) throw new Error(`HTTP ${resp.status}`);
    // Remove all local entries pointing to this server conversation
    for (const [id, c] of Object.entries(appState.conversations)) {
      if (c.serverId === serverId) {
        delete appState.conversations[id];
      }
    }
  } catch (err) {
    logApiError('deleteServerConversation', err);
    showToast('Delete failed', 'error');
  }
}

export async function searchConversations(query) {
  if (!appState.authToken || !query.trim()) return [];
  try {
    const resp = await fetch(`/api/conversations/search?q=${encodeURIComponent(query)}`, {
      headers: { 'Authorization': `Bearer ${appState.authToken}` },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (err) {
    logApiError('searchConversations', err);
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

export async function getDownloadURL(path) {
  const resp = await fetch('/api/files/download-url', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${appState.authToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ path }),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

// ── Agents ──

export async function listAgents() {
  const resp = await fetch('/api/agents', { headers: authHeaders() });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

export async function getAgent(id) {
  const resp = await fetch(`/api/agents/${id}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

export async function createAgent(payload) {
  const resp = await fetch('/api/agents', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Create agent failed');
  }
  return resp.json();
}

export async function updateAgent(id, payload) {
  const resp = await fetch(`/api/agents/${id}`, {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Update agent failed');
  }
  return resp.json();
}

export async function deleteAgent(id) {
  const resp = await fetch(`/api/agents/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (!resp.ok && resp.status !== 204) throw new Error(await resp.text());
}

export async function runAgent(id) {
  const resp = await fetch(`/api/agents/${id}/run`, {
    method: 'POST',
    headers: authHeaders(),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Trigger agent failed');
  }
  return resp.json();
}

export async function listAgentRuns(id, limit = 20) {
  const resp = await fetch(`/api/agents/${id}/runs?limit=${limit}`, {
    headers: authHeaders(),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

// ── Memory ──

export async function listMemories({ memory_type, source } = {}) {
  const qs = new URLSearchParams();
  if (memory_type) qs.set('memory_type', memory_type);
  if (source) qs.set('source', source);
  const url = '/api/memories' + (qs.toString() ? `?${qs}` : '');
  const resp = await fetch(url, { headers: authHeaders() });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

export async function getMemory(id) {
  const resp = await fetch(`/api/memories/${id}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

export async function createMemory(payload) {
  const resp = await fetch('/api/memories', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Create memory failed');
  }
  return resp.json();
}

export async function updateMemory(id, payload) {
  const resp = await fetch(`/api/memories/${id}`, {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Update memory failed');
  }
  return resp.json();
}

export async function deleteMemory(id) {
  const resp = await fetch(`/api/memories/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (!resp.ok && resp.status !== 204) throw new Error(await resp.text());
}

// ── User settings ──

export async function getUserSettings() {
  const resp = await fetch('/api/users/me/settings', { headers: authHeaders() });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

export async function updateUserSettings(payload) {
  const resp = await fetch('/api/users/me/settings', {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Update settings failed');
  }
  return resp.json();
}
