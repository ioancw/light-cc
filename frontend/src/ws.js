// WebSocket client with reconnection and event dispatch.

import { appState, clearAuth, getStreamingMessage, showToast } from './state.svelte.js';
import { fetchConversationHistory } from './api.js';

let ws = null;
let reconnectTimer = null;
let reconnectDelay = 1000;

export function connect() {
  if (ws && ws.readyState <= WebSocket.OPEN) return;

  appState.connecting = true;
  appState.connected = false;

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const tokenParam = appState.authToken
    ? `?token=${encodeURIComponent(appState.authToken)}`
    : '';

  ws = new WebSocket(`${protocol}//${location.host}/ws${tokenParam}`);

  ws.onopen = () => {
    appState.connected = true;
    appState.connecting = false;
    reconnectDelay = 1000;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  ws.onmessage = (event) => {
    const { type, data } = JSON.parse(event.data);
    handleEvent(type, data);
  };

  ws.onclose = (event) => {
    ws = null;
    appState.connected = false;
    appState.connecting = false;

    if (event.code === 4001) {
      clearAuth();
      return;
    }

    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 2, 30000);
      connect();
    }, reconnectDelay);
  };

  ws.onerror = () => ws.close();
}

export function disconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (ws) ws.close();
  ws = null;
}

export function send(type, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type, data }));
  }
}

// ── Event dispatch ──

function handleEvent(type, data) {
  const conv = appState.conversations[appState.currentId];
  const msg = getStreamingMessage();

  switch (type) {
    case 'connected':
      appState.sessionId = data.session_id;
      if (data.user) appState.user = data.user;
      appState.skills = data.skills || [];
      if (data.available_models) {
        appState.availableModels = data.available_models;
        appState.currentModel = data.model || data.available_models[0] || '';
        // Restore saved model preference
        const saved = localStorage.getItem('lcc_model');
        if (saved && data.available_models.includes(saved)) {
          if (saved !== data.model) send('set_model', { model: saved });
          appState.currentModel = saved;
        }
      }
      fetchConversationHistory();
      break;

    case 'model_changed':
      if (data.model) appState.currentModel = data.model;
      break;

    case 'conversation_loaded':
      if (conv && data.messages && data.messages.length > 0) {
        conv.messages = data.messages.map((m, i) => ({
          role: m.role,
          content: m.content || '',
          id: 'restored_' + i + '_' + Date.now(),
          toolCalls: (m.toolCalls || []).map(tc => ({
            id: tc.id || ('tc_' + i + '_' + Math.random().toString(36).slice(2, 8)),
            name: tc.name || 'tool',
            input: tc.input || {},
            result: tc.result || null,
            status: tc.status || 'done',
            is_error: tc.is_error || false,
            images: tc.images || null,
            chart: tc.chart || null,
          })),
          timestamp: m.timestamp || null,
          streaming: false,
        }));
      }
      if (data.context_tokens != null) {
        appState.totalTokens = data.context_tokens;
      }
      if (data.model) appState.currentModel = data.model;
      break;

    case 'text_delta':
      if (msg) msg.content += data.text;
      break;

    case 'tool_start':
      if (msg) {
        msg.toolCalls.push({
          id: data.tool_id,
          name: data.name,
          input: data.input,
          result: null,
          status: 'running',
          startTime: Date.now(),
        });
      }
      break;

    case 'tool_end':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          tc.status = data.is_error ? 'error' : 'done';
          tc.result = data.result;
          tc.duration = ((Date.now() - tc.startTime) / 1000).toFixed(1);
        }
      }
      break;

    case 'tool_stream':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          if (!tc.streamBuffer) tc.streamBuffer = '';
          tc.streamBuffer += data.text;
        }
      }
      break;

    case 'image':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          if (!tc.images) tc.images = [];
          tc.images.push({ mime: data.mime_type, data: data.data_base64, name: data.name });
        }
      }
      break;

    case 'table':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          if (!tc.tables) tc.tables = [];
          tc.tables.push(data.html);
        }
      }
      break;

    case 'chart':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          tc.chart = { title: data.title, plotlyJson: data.plotly_json };
        }
      }
      break;

    case 'html_embed':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          if (!tc.embeds) tc.embeds = [];
          tc.embeds.push({ name: data.name, html: data.html });
        }
      }
      break;

    case 'permission_request':
      appState.pendingPermission = {
        requestId: data.request_id,
        toolName: data.tool_name,
        summary: data.summary,
      };
      break;

    case 'notification':
      showToast(data.message || 'Notification', data.level === 'error' ? 'error' : 'info');
      break;

    case 'skills_updated':
      appState.skills = data.skills || [];
      break;

    case 'skill_activated':
      appState.inlineStatus = { message: `skill: ${data.name || 'unknown'}`, type: 'info' };
      setTimeout(() => {
        if (appState.inlineStatus?.message?.includes(data.name || 'unknown')) {
          appState.inlineStatus = null;
        }
      }, 4000);
      break;

    case 'response_end':
      if (msg) msg.streaming = false;
      appState.isStreaming = false;
      appState.inlineStatus = null;
      break;

    case 'generation_cancelled':
      if (msg) {
        msg.streaming = false;
        if (!msg.content) msg.content = '(cancelled)';
      }
      appState.isStreaming = false;
      appState.inlineStatus = null;
      break;

    case 'turn_complete':
      if (msg) msg.streaming = false;
      appState.isStreaming = false;
      appState.inlineStatus = null;
      if (data.conversation_id && conv) {
        conv.serverId = data.conversation_id;
      }
      if (data.context_tokens != null) {
        appState.totalTokens = data.context_tokens;
      }
      fetchConversationHistory();
      // Auto-generate title after first assistant response
      if (conv && conv.serverId && !conv.titleGenerated) {
        const assistantMsgs = conv.messages.filter(m => m.role === 'assistant' && m.content);
        if (assistantMsgs.length === 1) {
          conv.titleGenerated = true;
          send('generate_title', {});
        }
      }
      break;

    case 'conversation_forked': {
      const forkId = 'conv_' + Date.now();
      appState.conversations[forkId] = {
        id: forkId,
        serverId: data.conversation_id,
        title: (conv ? conv.title : 'Conversation') + ' (fork)',
        messages: conv ? [...conv.messages.map(m => ({ ...m, streaming: false }))] : [],
        createdAt: Date.now(),
        forkedFrom: data.source_conversation_id,
      };
      appState.currentId = forkId;
      break;
    }

    case 'title_updated':
      if (data.title) {
        for (const c of Object.values(appState.conversations)) {
          if (c.serverId === data.conversation_id) {
            c.title = data.title;
          }
        }
        // Also update the server conversations list so History shows correct titles
        for (const sc of (appState.serverConversations || [])) {
          if (sc.id === data.conversation_id) {
            sc.title = data.title;
          }
        }
      }
      break;

    case 'schedule_result': {
      const convId = data.conversation_id;
      const sname = data.schedule_name || 'Scheduled task';
      const slabel = data.status === 'completed' ? 'completed' : 'failed';
      showToast(`${sname} ${slabel} -- click to view`, slabel === 'failed' ? 'error' : 'info');
      // Refresh server conversations so the new one appears in the sidebar
      fetchConversationHistory();
      // If the user isn't mid-conversation, switch to the result
      if (convId && !appState.isStreaming) {
        const localId = 'conv_' + Date.now();
        appState.conversations[localId] = {
          id: localId,
          serverId: convId,
          title: `[Scheduled] ${sname}`,
          messages: [],
          createdAt: Date.now(),
          titleGenerated: true,
        };
        appState.currentId = localId;
        send('resume_conversation', { conversation_id: convId });
      }
      break;
    }

    case 'context_summarized':
      appState.totalTokens = 0;
      break;

    case 'error':
      if (msg) {
        msg.content += '\n\nError: ' + data.message;
        msg.streaming = false;
      }
      appState.isStreaming = false;
      break;
  }
}
