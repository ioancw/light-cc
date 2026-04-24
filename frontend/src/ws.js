// WebSocket client with reconnection and event dispatch.
// Supports multiplexed conversations via `cid` envelope field.

import { appState, clearAuth, showToast } from './state.svelte.js';
import { fetchConversationHistory } from './api.js';

let ws = null;
let reconnectTimer = null;
let reconnectDelay = 1000;

/** Open the WebSocket and wire up lifecycle handlers. No-op if already open/opening. */
export function connect() {
  if (ws && ws.readyState <= WebSocket.OPEN) return;

  appState.connecting = true;
  appState.connected = false;

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';

  ws = new WebSocket(`${protocol}//${location.host}/ws`);

  ws.onopen = () => {
    appState.connected = true;
    appState.connecting = false;
    appState.reconnectAt = null;
    reconnectDelay = 1000;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    // Send auth token as first message (preferred over query param)
    if (appState.authToken) {
      ws.send(JSON.stringify({ type: 'auth', data: { token: appState.authToken } }));
    }
  };

  ws.onmessage = (event) => {
    const { type, data, cid } = JSON.parse(event.data);
    dispatch(type, data, cid);
  };

  ws.onclose = (event) => {
    ws = null;
    appState.connected = false;
    appState.connecting = false;

    if (event.code === 4001) {
      appState.reconnectAt = null;
      clearAuth();
      return;
    }

    appState.reconnectAt = Date.now() + reconnectDelay;
    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 2, 30000);
      connect();
    }, reconnectDelay);
  };

  ws.onerror = (event) => {
    // Surface for devtools; onclose fires right after and drives the banner.
    console.error('[ws] connection error', event);
  };
}

/** Close the socket and cancel any pending reconnect. */
export function disconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (ws) ws.close();
  ws = null;
}

/**
 * Send an event to the server. Silently drops if the socket is not open.
 * @param {string} type - event name (e.g. `user_message`, `resume_conversation`)
 * @param {object} [data] - event payload
 * @param {string|null} [cid] - conversation id the event is scoped to
 */
export function send(type, data = {}, cid = null) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const msg = { type, data };
    if (cid) msg.cid = cid;
    // Track loading state for conversation resumes
    if (type === 'resume_conversation' && cid) {
      appState.loadingConversations.add(cid);
    }
    ws.send(JSON.stringify(msg));
  }
}

// ── Helpers ──

function findConvByCid(cid) {
  if (!cid) return null;
  // Match by serverId first, then by local id
  for (const c of Object.values(appState.conversations)) {
    if (c.serverId === cid || c.id === cid) return c;
  }
  return null;
}

function getStreamingMsg(conv) {
  if (!conv) return null;
  return conv.messages.find(m => m.streaming) || null;
}

// ── Event handlers ──
// Each handler receives (data, ctx) where ctx bundles the resolved conversation
// and streaming message for the envelope's cid (null for global events).

function onConnected(data) {
  appState.sessionId = data.session_id;
  if (data.user) appState.user = data.user;
  appState.skills = data.skills || [];
  appState.agents = data.agents || [];
  appState.suggestions = data.suggestions || [];
  if (data.available_models) {
    appState.availableModels = data.available_models;
    appState.currentModel = data.model || data.available_models[0] || '';
    const saved = localStorage.getItem('lcc_model');
    if (saved && data.available_models.includes(saved)) {
      if (saved !== data.model) send('set_model', { model: saved });
      appState.currentModel = saved;
    }
  }
  // If a turn was streaming when the socket dropped, resubscribe. The
  // server replies with generation_state{is_generating} so we can either
  // keep the spinner (turn still running, events will resume) or clear
  // it and reload messages from the DB (turn finished while offline).
  for (const conv of Object.values(appState.conversations)) {
    if (conv.messages && conv.messages.some(m => m.streaming)) {
      send('subscribe_cid', {}, conv.id);
    }
  }
  fetchConversationHistory();
}

function onGenerationState(data, { conv }) {
  // Server's authoritative view of whether a cid is currently generating.
  // When the turn has already finished, reload messages from the DB and
  // clear the stale streaming placeholder.
  if (!conv) return;
  if (!data.is_generating) {
    const streamingMsg = getStreamingMsg(conv);
    if (streamingMsg) {
      streamingMsg.streaming = false;
      if (!streamingMsg.content && (!streamingMsg.toolCalls || streamingMsg.toolCalls.length === 0)) {
        // Empty placeholder -- drop it so the server-loaded messages don't duplicate.
        conv.messages = conv.messages.filter(m => m !== streamingMsg);
      }
    }
    if (conv.serverId) {
      send('resume_conversation', { conversation_id: conv.serverId }, conv.id);
    }
  }
}

function onModelChanged(data, { conv }) {
  if (!data.model) return;
  // If scoped to a conversation, update that conversation's model
  if (conv) conv.model = data.model;
  // Always update the global current model for display
  appState.currentModel = data.model;
}

function onConversationLoaded(data, { conv, cid }) {
  if (cid) appState.loadingConversations.delete(cid);
  if (conv) conv.stub = false;
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
      model: m.model || null,
      streaming: false,
    }));
    // Server says the cid is still generating -- add a streaming
    // placeholder so incoming text_delta/tool_start events have a
    // destination. This is the reconnect-into-a-live-turn path.
    if (data.is_generating) {
      conv.messages.push({
        role: 'assistant',
        content: '',
        id: 'msg_' + Date.now(),
        toolCalls: [],
        streaming: true,
        timestamp: Date.now(),
      });
    }
  }
  if (data.context_tokens != null && conv) {
    conv.totalTokens = data.context_tokens;
  }
  if (data.model) appState.currentModel = data.model;
}

function onTextDelta(data, { msg }) {
  if (msg) msg.content += data.text;
}

function onToolStart(data, { msg }) {
  if (!msg) return;
  msg.toolCalls.push({
    id: data.tool_id,
    name: data.name,
    input: data.input,
    description: data.description || '',
    result: null,
    status: 'running',
    startTime: Date.now(),
  });
}

function onToolEnd(data, { msg }) {
  if (!msg) return;
  const tc = msg.toolCalls.find(t => t.id === data.tool_id);
  if (tc) {
    tc.status = data.is_error ? 'error' : 'done';
    tc.result = data.result;
    tc.duration = ((Date.now() - tc.startTime) / 1000).toFixed(1);
  }
}

function onToolStream(data, { msg }) {
  if (!msg) return;
  const tc = msg.toolCalls.find(t => t.id === data.tool_id);
  if (tc) {
    if (!tc.streamBuffer) tc.streamBuffer = '';
    tc.streamBuffer += data.text;
  }
}

function onImage(data, { msg }) {
  if (!msg) return;
  const tc = msg.toolCalls.find(t => t.id === data.tool_id);
  if (tc) {
    if (!tc.images) tc.images = [];
    tc.images.push({ mime: data.mime_type, data: data.data_base64, name: data.name });
  }
}

function onTable(data, { msg }) {
  if (!msg) return;
  const tc = msg.toolCalls.find(t => t.id === data.tool_id);
  if (tc) {
    if (!tc.tables) tc.tables = [];
    tc.tables.push(data.html);
  }
}

function onChart(data, { msg }) {
  if (!msg) return;
  const tc = msg.toolCalls.find(t => t.id === data.tool_id);
  if (tc) tc.chart = { title: data.title, plotlyJson: data.plotly_json };
}

function onD3Chart(data, { msg }) {
  if (!msg) return;
  const tc = msg.toolCalls.find(t => t.id === data.tool_id);
  if (tc) tc.d3Chart = { title: data.title, spec: data.spec };
}

function onHtmlEmbed(data, { msg }) {
  if (!msg) return;
  const tc = msg.toolCalls.find(t => t.id === data.tool_id);
  if (tc) {
    if (!tc.embeds) tc.embeds = [];
    tc.embeds.push({ name: data.name, html: data.html });
  }
}

function onPermissionRequest(data, { cid }) {
  if (!cid) return;
  appState.pendingPermissions[cid] = {
    cid,
    requestId: data.request_id,
    toolName: data.tool_name,
    summary: data.summary,
  };
}

function onNotification(data) {
  showToast(data.message || 'Notification', data.level === 'error' ? 'error' : 'info');
}

function onSkillsUpdated(data) {
  appState.skills = data.skills || [];
}

function onAgentsUpdated(data) {
  // Backend ships the refreshed roster; older builds may send `{}`,
  // in which case we leave the existing list alone rather than wiping it.
  if (Array.isArray(data.agents)) appState.agents = data.agents;
}

function onSkillActivated(data) {
  const name = data.name || 'unknown';
  appState.inlineStatus = { message: `skill: ${name}`, type: 'info' };
  setTimeout(() => {
    if (appState.inlineStatus?.message?.includes(name)) {
      appState.inlineStatus = null;
    }
  }, 4000);
}

function onResponseEnd(_data, { msg }) {
  if (msg) msg.streaming = false;
  appState.inlineStatus = null;
}

function onGenerationCancelled(_data, { msg }) {
  if (msg) {
    msg.streaming = false;
    if (!msg.content) msg.content = '(cancelled)';
  }
  appState.inlineStatus = null;
}

function onTurnComplete(data, { conv, msg }) {
  if (msg) {
    msg.streaming = false;
    if (data.model) msg.model = data.model;
  }
  appState.inlineStatus = null;
  if (data.conversation_id && conv) {
    conv.serverId = data.conversation_id;
  }
  if (data.context_tokens != null && conv) {
    conv.totalTokens = data.context_tokens;
  }
  if (conv) conv.updatedAt = Date.now();
  fetchConversationHistory();
  // Auto-generate title after first assistant response
  if (conv && conv.serverId && !conv.titleGenerated) {
    const assistantMsgs = conv.messages.filter(m => m.role === 'assistant' && m.content);
    if (assistantMsgs.length === 1) {
      conv.titleGenerated = true;
      send('generate_title', {}, conv.id);
    }
  }
}

function onConversationForked(data, { conv }) {
  const forkId = 'conv_' + Date.now();
  appState.conversations[forkId] = {
    id: forkId,
    serverId: data.conversation_id,
    title: (conv ? conv.title : 'Conversation') + ' (fork)',
    messages: conv ? [...conv.messages.map(m => ({ ...m, streaming: false }))] : [],
    createdAt: Date.now(),
    forkedFrom: data.source_conversation_id,
    titleGenerated: true,
    totalTokens: 0,
  };
  appState.currentId = forkId;
}

function onTitleUpdated(data) {
  if (!data.title) return;
  for (const c of Object.values(appState.conversations)) {
    if (c.serverId === data.conversation_id) {
      c.title = data.title;
    }
  }
}

function onAgentResult(data) {
  const aname = data.agent_name || 'Agent';
  const alabel = data.status === 'completed' ? 'completed' : 'failed';
  showToast(`[Agent] ${aname} ${alabel}`, alabel === 'failed' ? 'error' : 'info');
  fetchConversationHistory();
  window.dispatchEvent(new CustomEvent('agent_result', { detail: data }));
}

function onScheduleResult(data) {
  const convId = data.conversation_id;
  const sname = data.schedule_name || 'Scheduled task';
  const slabel = data.status === 'completed' ? 'completed' : 'failed';
  showToast(`${sname} ${slabel} -- click to view`, slabel === 'failed' ? 'error' : 'info');
  fetchConversationHistory();
  // If the user isn't mid-conversation in current chat, switch to the result
  const currentConv = appState.conversations[appState.currentId];
  const currentBusy = currentConv && currentConv.messages.some(m => m.streaming);
  if (convId && !currentBusy) {
    const localId = 'conv_' + Date.now();
    appState.conversations[localId] = {
      id: localId,
      serverId: convId,
      title: `[Scheduled] ${sname}`,
      messages: [],
      createdAt: Date.now(),
      titleGenerated: true,
      totalTokens: 0,
    };
    appState.currentId = localId;
    send('resume_conversation', { conversation_id: convId }, localId);
  }
}

function onContextSummarized(_data, { conv }) {
  if (conv) conv.totalTokens = 0;
}

function onError(data, { cid, msg }) {
  if (cid) appState.loadingConversations.delete(cid);
  if (msg) {
    msg.content += '\n\nError: ' + data.message;
    msg.streaming = false;
  }
}

const handlers = {
  connected: onConnected,
  generation_state: onGenerationState,
  model_changed: onModelChanged,
  conversation_loaded: onConversationLoaded,
  text_delta: onTextDelta,
  tool_start: onToolStart,
  tool_end: onToolEnd,
  tool_stream: onToolStream,
  image: onImage,
  table: onTable,
  chart: onChart,
  d3_chart: onD3Chart,
  html_embed: onHtmlEmbed,
  permission_request: onPermissionRequest,
  notification: onNotification,
  skills_updated: onSkillsUpdated,
  agents_updated: onAgentsUpdated,
  skill_activated: onSkillActivated,
  response_end: onResponseEnd,
  generation_cancelled: onGenerationCancelled,
  turn_complete: onTurnComplete,
  conversation_forked: onConversationForked,
  title_updated: onTitleUpdated,
  agent_result: onAgentResult,
  schedule_result: onScheduleResult,
  context_summarized: onContextSummarized,
  error: onError,
};

function dispatch(type, data, cid = null) {
  const handler = handlers[type];
  if (!handler) return;
  // For cid-scoped events, look up the target conversation.
  // For global events (connected, skills_updated, etc.), conv will be null.
  const conv = cid ? findConvByCid(cid) : appState.conversations[appState.currentId];
  const msg = getStreamingMsg(conv);
  handler(data, { conv, msg, cid });
}
