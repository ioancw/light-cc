// Global reactive state using Svelte 5 runes.
// Import this module in any component that needs shared state.

export const appState = $state({
  // Auth
  authToken: localStorage.getItem('lcc_access_token') || null,
  refreshToken: localStorage.getItem('lcc_refresh_token') || null,
  user: JSON.parse(localStorage.getItem('lcc_user') || 'null'),

  // Connection
  connected: false,
  connecting: false,
  sessionId: null,

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

  // Suggestion chips for new-chat empty state
  suggestions: [],

  // Permission requests keyed by conversation id (supports concurrent agents)
  pendingPermissions: {},

  // Theme
  theme: localStorage.getItem('lcc_theme') || 'midnight',

  // Sidebar
  sidebarCollapsed: localStorage.getItem('lcc_sidebar_collapsed') === '1',

  // Toasts (notification messages)
  toasts: [],

  // Inline status messages (shown in input footer instead of toasts)
  inlineStatus: null, // { message, type } or null

  // Loading conversations (set of cids currently being loaded from server)
  loadingConversations: new Set(),

  // Scroll state (shared between ChatArea and InputBar)
  needsScrollDown: false,
  scrollToBottom: null, // function ref set by ChatArea
});

// Svelte 5 does not allow exporting $derived from modules.
// Export getter functions instead -- components use these reactively.
export function isAuthenticated() {
  return !!appState.authToken;
}

export function currentConversation() {
  return appState.currentId ? appState.conversations[appState.currentId] : null;
}

export function isCurrentStreaming() {
  const conv = currentConversation();
  return conv ? conv.messages.some(m => m.streaming) : false;
}

export function isAnyStreaming() {
  return Object.values(appState.conversations).some(
    conv => conv.messages.some(m => m.streaming)
  );
}

export function sortedConversations() {
  return Object.values(appState.conversations)
    .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0));
}

// Helpers to persist auth state
export function setAuth(data) {
  appState.authToken = data.access_token;
  appState.refreshToken = data.refresh_token;
  appState.user = data.user;
  localStorage.setItem('lcc_access_token', data.access_token);
  localStorage.setItem('lcc_refresh_token', data.refresh_token);
  localStorage.setItem('lcc_user', JSON.stringify(data.user));
}

export function clearAuth() {
  appState.authToken = null;
  appState.refreshToken = null;
  appState.user = null;
  localStorage.removeItem('lcc_access_token');
  localStorage.removeItem('lcc_refresh_token');
  localStorage.removeItem('lcc_user');
}

// Conversation helpers
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

export function switchConversation(id) {
  appState.currentId = id;
  appState.totalTokens = 0;
}

// Toast helper
export function showToast(message, type = 'info', duration = 2500) {
  const id = 'toast_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
  appState.toasts.push({ id, message, type });
  setTimeout(() => {
    appState.toasts = appState.toasts.filter(t => t.id !== id);
  }, duration);
}

// Per-conversation permission helpers
export function setPendingPermission(cid, perm) {
  appState.pendingPermissions[cid] = perm;
}

export function clearPendingPermission(cid) {
  delete appState.pendingPermissions[cid];
}

export function getPendingPermission(cid) {
  return appState.pendingPermissions[cid] || null;
}

// Backward-compat: pendingPermission for the current conversation
export function pendingPermission() {
  const id = appState.currentId;
  if (!id) return null;
  const conv = appState.conversations[id];
  const serverId = conv?.serverId;
  return appState.pendingPermissions[id] || (serverId && appState.pendingPermissions[serverId]) || null;
}

export function isConversationLoading(cid) {
  return cid && appState.loadingConversations.has(cid);
}

export function getStreamingMessage(convId = null) {
  const conv = convId ? appState.conversations[convId] : appState.conversations[appState.currentId];
  if (!conv) return null;
  return conv.messages.find(m => m.streaming);
}
