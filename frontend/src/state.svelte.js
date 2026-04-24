// Global reactive state using Svelte 5 runes.
// Import this module in any component that needs shared state.

import { SvelteSet } from 'svelte/reactivity';

/**
 * @typedef {Object} ToolCall
 * @property {string} id
 * @property {string} name
 * @property {object} input
 * @property {string} [description]
 * @property {*} result
 * @property {'running'|'done'|'error'} status
 * @property {boolean} [is_error]
 * @property {number} [startTime]
 * @property {string} [duration]
 * @property {string} [streamBuffer]
 * @property {Array<{mime:string,data:string,name:string}>} [images]
 * @property {string[]} [tables]
 * @property {{title:string,plotlyJson:string}} [chart]
 * @property {{title:string,spec:object}} [d3Chart]
 * @property {Array<{name:string,html:string}>} [embeds]
 */

/**
 * @typedef {Object} Message
 * @property {'user'|'assistant'} role
 * @property {string} content
 * @property {string} id
 * @property {ToolCall[]} toolCalls
 * @property {boolean} streaming
 * @property {number} [timestamp]
 * @property {string} [model]
 */

/**
 * @typedef {Object} Conversation
 * @property {string} id             Local id (conv_* or srv_*)
 * @property {string|null} serverId  Persisted backend id, assigned after first turn
 * @property {string} title
 * @property {Message[]} messages
 * @property {number} createdAt
 * @property {number} [updatedAt]
 * @property {boolean} titleGenerated
 * @property {boolean} [pinned]
 * @property {boolean} [stub]        True until hydrated from the server
 * @property {string} [forkedFrom]
 * @property {number} totalTokens
 * @property {string} [model]
 */

/**
 * @typedef {Object} PendingPermission
 * @property {string} cid
 * @property {string} requestId
 * @property {string} toolName
 * @property {string} summary
 */

export const appState = $state({
  // Auth
  authToken: localStorage.getItem('lcc_access_token') || null,
  refreshToken: localStorage.getItem('lcc_refresh_token') || null,
  user: JSON.parse(localStorage.getItem('lcc_user') || 'null'),

  // Connection
  connected: false,
  connecting: false,
  sessionId: null,
  // ms epoch of the next reconnect attempt (null when connected or giving up).
  reconnectAt: null,

  // Conversations
  conversations: {},
  currentId: null,

  // Model
  availableModels: [],
  currentModel: '',

  // Streaming (per-conversation now -- isStreaming removed, use isCurrentStreaming())
  totalTokens: 0,  // legacy, prefer conv.totalTokens

  // Skills (for slash command autocomplete)
  skills: [],

  // Agents (for `@agent-` autocomplete picker)
  agents: [],

  // Suggestion chips for new-chat empty state
  suggestions: [],

  // Permission requests keyed by conversation id (supports concurrent agents)
  pendingPermissions: {},

  // Theme
  theme: localStorage.getItem('lcc_theme') || 'midnight',

  // Sidebar. Default-collapsed on mobile when no prior preference exists.
  sidebarCollapsed: (() => {
    const stored = localStorage.getItem('lcc_sidebar_collapsed');
    if (stored !== null) return stored === '1';
    return typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches;
  })(),

  // Toasts (notification messages)
  toasts: [],

  // Inline status messages (shown in input footer instead of toasts)
  inlineStatus: null, // { message, type } or null

  // Loading conversations (set of cids currently being loaded from server)
  loadingConversations: new SvelteSet(),

  // Scroll state (shared between ChatArea and InputBar)
  needsScrollDown: false,
  scrollToBottom: null, // function ref set by ChatArea
});

// Reactive viewport state. `isMobile` is kept in sync with a matchMedia listener.
export const viewport = $state({
  isMobile: typeof window !== 'undefined'
    ? window.matchMedia('(max-width: 768px)').matches
    : false,
});
if (typeof window !== 'undefined') {
  const mq = window.matchMedia('(max-width: 768px)');
  const onChange = (e) => { viewport.isMobile = e.matches; };
  if (mq.addEventListener) mq.addEventListener('change', onChange);
  else mq.addListener(onChange);
}

// Svelte 5 does not allow exporting $derived from modules.
// Export getter functions instead -- components use these reactively.

/** @returns {boolean} */
export function isAuthenticated() {
  return !!appState.authToken;
}

/** @returns {Conversation|null} */
export function currentConversation() {
  return appState.currentId ? appState.conversations[appState.currentId] : null;
}

/** @returns {boolean} */
export function isCurrentStreaming() {
  const conv = currentConversation();
  return conv ? conv.messages.some(m => m.streaming) : false;
}

/** @returns {boolean} */
export function isAnyStreaming() {
  return Object.values(appState.conversations).some(
    conv => conv.messages.some(m => m.streaming)
  );
}

/** @returns {Conversation[]} Conversations ordered by most recent activity first. */
export function sortedConversations() {
  return Object.values(appState.conversations)
    .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0));
}

/**
 * Persist auth tokens to localStorage and push into reactive state.
 * @param {{access_token:string, refresh_token:string, user:object}} data
 */
export function setAuth(data) {
  appState.authToken = data.access_token;
  appState.refreshToken = data.refresh_token;
  appState.user = data.user;
  localStorage.setItem('lcc_access_token', data.access_token);
  localStorage.setItem('lcc_refresh_token', data.refresh_token);
  localStorage.setItem('lcc_user', JSON.stringify(data.user));
}

/** Wipe auth tokens from state and localStorage. */
export function clearAuth() {
  appState.authToken = null;
  appState.refreshToken = null;
  appState.user = null;
  localStorage.removeItem('lcc_access_token');
  localStorage.removeItem('lcc_refresh_token');
  localStorage.removeItem('lcc_user');
}

/**
 * Create a fresh local conversation and switch to it.
 * @returns {string} local id of the new conversation
 */
export function newConversation() {
  const id = 'conv_' + Date.now();
  appState.conversations[id] = {
    id,
    serverId: null,
    title: 'New conversation',
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
    titleGenerated: false,
    pinned: false,
    totalTokens: 0,
  };
  appState.currentId = id;
  appState.totalTokens = 0;
  return id;
}

/** @param {string} id */
export function switchConversation(id) {
  appState.currentId = id;
  appState.totalTokens = 0;
}

/**
 * Push a toast notification; auto-clears after `duration` ms.
 * @param {string} message
 * @param {'info'|'success'|'error'} [type]
 * @param {number} [duration]
 */
export function showToast(message, type = 'info', duration = 2500) {
  const id = 'toast_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
  appState.toasts.push({ id, message, type });
  setTimeout(() => {
    appState.toasts = appState.toasts.filter(t => t.id !== id);
  }, duration);
}

/**
 * @param {string} cid
 * @param {PendingPermission} perm
 */
export function setPendingPermission(cid, perm) {
  appState.pendingPermissions[cid] = perm;
}

/** @param {string} cid */
export function clearPendingPermission(cid) {
  delete appState.pendingPermissions[cid];
}

/**
 * @param {string} cid
 * @returns {PendingPermission|null}
 */
export function getPendingPermission(cid) {
  return appState.pendingPermissions[cid] || null;
}

/**
 * Permission request for the current conversation, checked under both its
 * local id and its serverId (the server may address either).
 * @returns {PendingPermission|null}
 */
export function pendingPermission() {
  const id = appState.currentId;
  if (!id) return null;
  const conv = appState.conversations[id];
  const serverId = conv?.serverId;
  return appState.pendingPermissions[id] || (serverId && appState.pendingPermissions[serverId]) || null;
}

/** @param {string} cid */
export function isConversationLoading(cid) {
  return cid && appState.loadingConversations.has(cid);
}

/**
 * @param {string|null} [convId] Defaults to the current conversation.
 * @returns {Message|undefined}
 */
export function getStreamingMessage(convId = null) {
  const conv = convId ? appState.conversations[convId] : appState.conversations[appState.currentId];
  if (!conv) return null;
  return conv.messages.find(m => m.streaming);
}
