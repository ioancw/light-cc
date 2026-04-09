// ═══════════════════════════════════════
// STATE
// ═══════════════════════════════════════
let state = {
  ws: null,
  sessionId: null,
  conversations: {},
  currentId: null,
  isStreaming: false,
  totalTokens: 0,
  reconnectTimer: null,
};

function currentConv() {
  return state.conversations[state.currentId];
}

// ═══════════════════════════════════════
// CHART THEME — matches CSS :root vars
// ═══════════════════════════════════════
const CHART_THEME = {
  colorway: [
    '#818cf8', '#38bdf8', '#10b981', '#f59e0b', '#ef4444',
    '#a78bfa', '#fb923c', '#e879f9', '#2dd4bf', '#f472b6',
    '#6366f1', '#facc15',
  ],
  font: { family: 'Geist Mono, monospace', size: 11, color: '#c4c4d4' },
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  margin: { l: 48, r: 16, t: 44, b: 40 },
  title: { font: { size: 14, color: '#e8e8f2' }, x: 0, xanchor: 'left' },
  xaxis: { gridcolor: '#1e1e26', zerolinecolor: '#28282e', linecolor: '#28282e',
           tickfont: { size: 10, color: '#8888a0' }, title: { font: { size: 11, color: '#8888a0' } } },
  yaxis: { gridcolor: '#1e1e26', zerolinecolor: '#28282e', linecolor: '#28282e',
           tickfont: { size: 10, color: '#8888a0' }, title: { font: { size: 11, color: '#8888a0' } } },
  legend: { bgcolor: 'rgba(0,0,0,0)', borderwidth: 0, font: { size: 10, color: '#8888a0' } },
  hoverlabel: { bgcolor: '#16161c', bordercolor: '#28282e',
                font: { family: 'Geist Mono, monospace', size: 11, color: '#e8e8f2' } },
  polar: { bgcolor: 'rgba(0,0,0,0)',
           radialaxis: { gridcolor: '#1e1e26', linecolor: '#28282e', tickfont: { size: 9, color: '#5a5a72' } },
           angularaxis: { gridcolor: '#1e1e26', linecolor: '#28282e', tickfont: { size: 10, color: '#8888a0' } } },
};

// Lazy-load Plotly on first use
let _plotlyPromise = null;
function loadPlotly() {
  if (window.Plotly) return Promise.resolve();
  if (_plotlyPromise) return _plotlyPromise;
  _plotlyPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.plot.ly/plotly-2.35.0.min.js';
    script.charset = 'utf-8';
    script.onload = resolve;
    script.onerror = () => reject(new Error('Failed to load Plotly'));
    document.head.appendChild(script);
  });
  return _plotlyPromise;
}

async function renderPlotlyChart(divId, plotlyJson) {
  const el = document.getElementById(divId);
  if (!el || !plotlyJson) return;
  try {
    await loadPlotly();
    const fig = JSON.parse(plotlyJson);
    const layout = deepMerge(deepMerge({}, CHART_THEME), fig.layout || {});
    layout.paper_bgcolor = CHART_THEME.paper_bgcolor;
    layout.plot_bgcolor = CHART_THEME.plot_bgcolor;
    layout.font = CHART_THEME.font;
    layout.hoverlabel = CHART_THEME.hoverlabel;
    layout.autosize = true;
    delete layout.width;
    delete layout.height;

    Plotly.newPlot(el, fig.data, layout, {
      responsive: true,
      displayModeBar: false,
    });
  } catch (e) {
    console.error('Plotly render error:', e);
  }
}

function deepMerge(target, source) {
  for (const key of Object.keys(source)) {
    if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])
        && target[key] && typeof target[key] === 'object' && !Array.isArray(target[key])) {
      deepMerge(target[key], source[key]);
    } else {
      target[key] = source[key];
    }
  }
  return target;
}

function hydrateEmbeds(toolId, embeds) {
  const frames = document.querySelectorAll(`iframe[data-embed-tool="${toolId}"]`);
  frames.forEach((frame, i) => {
    if (i < embeds.length) {
      const doc = frame.contentDocument || frame.contentWindow?.document;
      if (doc) {
        doc.open();
        doc.write(embeds[i].html);
        doc.close();
      }
    }
  });
}

// ═══════════════════════════════════════
// INIT
// ═══════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  // Auth check — redirect to login if no token
  const token = localStorage.getItem('lcc_access_token');
  if (!token) {
    window.location.href = '/login';
    return;
  }
  state.authToken = token;
  state.user = JSON.parse(localStorage.getItem('lcc_user') || '{}');

  // OS theme auto-detection (only if user hasn't set a preference)
  const savedTheme = localStorage.getItem('lcc_theme');
  if (!savedTheme) {
    const preferLight = window.matchMedia('(prefers-color-scheme: light)').matches;
    setTheme(preferLight ? 'light' : 'midnight');
  }

  initEventListeners();
  newChat();
  connectWebSocket();
});

function initEventListeners() {
  // Sidebar
  document.getElementById('sidebarToggle').addEventListener('click', toggleSidebar);
  document.getElementById('sidebarOverlay').addEventListener('click', toggleSidebar);
  document.getElementById('sidebarOpenBtn').addEventListener('click', collapseSidebar);
  document.getElementById('sidebarCloseBtn').addEventListener('click', collapseSidebar);
  document.getElementById('newChatBtn').addEventListener('click', newChat);
  document.getElementById('importBtn').addEventListener('click', importConversation);
  document.getElementById('logoutBtn').addEventListener('click', logout);
  document.getElementById('convSearch').addEventListener('input', (e) => onConvSearch(e.target.value));

  // Theme dots
  document.querySelectorAll('.theme-dot').forEach(dot => {
    dot.addEventListener('click', () => setTheme(dot.dataset.theme));
    dot.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setTheme(dot.dataset.theme); }
    });
  });

  // Topbar
  document.getElementById('modelSelector').addEventListener('change', (e) => onModelChange(e.target.value));
  document.getElementById('viewToggle').addEventListener('click', toggleCompactView);
  document.getElementById('exportBtn').addEventListener('click', exportConversation);
  document.getElementById('filesBtn').addEventListener('click', toggleFilePanel);
  document.getElementById('clearBtn').addEventListener('click', clearChat);

  // Search bar
  document.getElementById('convSearchInput').addEventListener('input', (e) => onConvSearchInput(e.target.value));
  document.getElementById('convSearchInput').addEventListener('keydown', (e) => onConvSearchKey(e));
  document.getElementById('convSearchPrevBtn').addEventListener('click', convSearchPrev);
  document.getElementById('convSearchNextBtn').addEventListener('click', convSearchNext);
  document.getElementById('convSearchCloseBtn').addEventListener('click', closeConvSearch);

  // Context warning
  document.getElementById('contextSummarizeBtn').addEventListener('click', truncateContext);
  document.getElementById('contextDismissBtn').addEventListener('click', dismissContextWarning);

  // System prompt
  document.getElementById('syspromptToggle').addEventListener('click', toggleSysPrompt);
  document.getElementById('syspromptTextarea').addEventListener('input', handleSysPromptChange);

  // Input
  const textarea = document.getElementById('inputTextarea');
  textarea.addEventListener('keydown', (e) => handleKeydown(e));
  textarea.addEventListener('input', () => { autoResize(textarea); updateAutocomplete(textarea); });

  // Send / Stop
  document.getElementById('sendBtn').addEventListener('click', sendMessage);
  document.getElementById('stopBtn').addEventListener('click', cancelGeneration);

  // Scroll to bottom
  document.getElementById('scrollBottomBtn').addEventListener('click', scrollToBottom);

  // File panel
  document.getElementById('filePanelCloseBtn').addEventListener('click', toggleFilePanel);
  document.getElementById('fileBreadcrumbRoot').addEventListener('click', () => navigateFiles(''));
  document.getElementById('fileUploadBtn').addEventListener('click', uploadFile);
  document.getElementById('fileRefreshBtn').addEventListener('click', refreshFiles);

  // Undo toast
  document.getElementById('undoToastBtn').addEventListener('click', undoDelete);

  // Shortcuts overlay
  document.getElementById('shortcutsOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) toggleShortcuts();
  });

  // Suggestion chips (static ones in HTML)
  document.querySelectorAll('.suggestion-chip[data-suggestion]').forEach(chip => {
    chip.addEventListener('click', () => sendSuggestion(chip.dataset.suggestion));
  });
}

// ═══════════════════════════════════════
// WEBSOCKET
// ═══════════════════════════════════════
function connectWebSocket() {
  updateConnectionStatus('connecting');
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const tokenParam = state.authToken ? `?token=${encodeURIComponent(state.authToken)}` : '';
  const ws = new WebSocket(`${protocol}//${location.host}/ws${tokenParam}`);

  ws.onopen = () => {
    state.ws = ws;
    updateConnectionStatus('connected');
    if (state.reconnectTimer) {
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
    }
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    handleServerEvent(msg.type, msg.data);
  };

  ws.onclose = (event) => {
    state.ws = null;
    if (event.code === 4001) {
      // Auth failed — clear token and redirect to login
      localStorage.removeItem('lcc_access_token');
      localStorage.removeItem('lcc_refresh_token');
      localStorage.removeItem('lcc_user');
      window.location.href = '/login';
      return;
    }
    updateConnectionStatus('disconnected');
    state.reconnectTimer = setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = () => {
    ws.close();
  };
}

function updateConnectionStatus(status) {
  const el = document.getElementById('connectionStatus');
  const dot = document.getElementById('statusDot');
  if (status === 'connected') {
    el.textContent = 'connected';
    el.className = 'connection-status connected';
    dot.style.background = 'var(--green)';
    dot.style.boxShadow = '0 0 6px var(--green)';
  } else if (status === 'connecting') {
    el.textContent = 'connecting...';
    el.className = 'connection-status connecting';
    dot.style.background = 'var(--amber)';
    dot.style.boxShadow = '0 0 6px var(--amber)';
  } else {
    el.textContent = 'disconnected — reconnecting...';
    el.className = 'connection-status disconnected';
    dot.style.background = 'var(--red)';
    dot.style.boxShadow = '0 0 6px var(--red)';
  }
}

function sendWsEvent(type, data) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type, data }));
  }
}

// ═══════════════════════════════════════
// SERVER EVENT HANDLER
// ═══════════════════════════════════════
function handleServerEvent(type, data) {
  const conv = currentConv();
  const msg = conv ? getStreamingMsg(conv) : null;

  switch (type) {
    case 'connected':
      state.sessionId = data.session_id;
      if (data.user) {
        state.user = data.user;
      }
      // Store skills for autocomplete
      state.skills = data.skills || [];
      // Populate model selector
      if (data.available_models) {
        populateModelSelector(data.available_models, data.model);
      }
      // Fetch saved conversations from server
      fetchConversationHistory();
      break;

    case 'model_changed':
      if (data.model) {
        const sel = document.getElementById('modelSelector');
        if (sel) sel.value = data.model;
        showStatus(`model: ${data.model}`);
      }
      break;

    case 'conversation_loaded': {
      // Server loaded a conversation into the session
      if (data.model) {
        const sel = document.getElementById('modelSelector');
        if (sel) sel.value = data.model;
      }
      // Render restored messages into the current conversation
      const rConv = currentConv();
      if (rConv && data.messages && data.messages.length > 0) {
        rConv.messages = data.messages.map((m, i) => ({
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
        }));
        renderMessages();
        const container = document.getElementById('messages');
        container.scrollTop = container.scrollHeight;
      }
      // Update context token count from server
      if (data.context_tokens != null) {
        state.totalTokens = data.context_tokens;
        document.getElementById('tokenCount').textContent = data.context_tokens.toLocaleString();
        checkContextUsage();
      }
      showStatus(`loaded ${data.message_count} messages`);
      break;
    }

    case 'text_delta':
      if (msg) {
        msg.content += data.text;
        renderStreamingMessage(msg);
      }
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
        renderStreamingMessage(msg);
        updateThinkingStatus(`Running ${data.name}...`);
      }
      break;

    case 'tool_end':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          tc.status = data.is_error ? 'error' : 'done';
          tc.result = data.result;
          tc.streaming = false;
          tc.duration = ((Date.now() - tc.startTime) / 1000).toFixed(1);
        }
        finalizeToolStream(data.tool_id);
        renderStreamingMessage(msg);
        updateThinkingStatus('Generating...');
      }
      break;

    case 'tool_stream':
      if (msg) {
        appendToolStream(data.tool_id, data.text);
      }
      break;

    case 'image':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          if (!tc.images) tc.images = [];
          tc.images.push({ mime: data.mime_type, data: data.data_base64, name: data.name });
        }
        renderStreamingMessage(msg);
      }
      break;

    case 'table':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          if (!tc.tables) tc.tables = [];
          tc.tables.push(data.html);
        }
        renderStreamingMessage(msg);
      }
      break;

    case 'chart':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          tc.chart = { title: data.title, plotlyJson: data.plotly_json };
        }
        renderStreamingMessage(msg);
        setTimeout(() => renderPlotlyChart('chart-' + data.tool_id, data.plotly_json), 50);
      }
      break;

    case 'html_embed':
      if (msg) {
        const tc = msg.toolCalls.find(t => t.id === data.tool_id);
        if (tc) {
          if (!tc.embeds) tc.embeds = [];
          tc.embeds.push({ name: data.name, html: data.html });
        }
        renderStreamingMessage(msg);
        // Write HTML into iframe after DOM is ready
        setTimeout(() => {
          const frames = document.querySelectorAll(`iframe[data-embed-tool="${data.tool_id}"]`);
          frames.forEach((frame, i) => {
            if (!frame.dataset.loaded) {
              const doc = frame.contentDocument || frame.contentWindow.document;
              doc.open();
              doc.write(tc.embeds[i].html);
              doc.close();
              frame.dataset.loaded = '1';
            }
          });
        }, 30);
      }
      break;

    case 'permission_request':
      showPermissionDialog(data.request_id, data.tool_name, data.summary);
      break;

    case 'notification':
      showStatus(data.message.slice(0, 120));
      break;

    case 'skill_activated':
      updateThinkingStatus(`Using ${data.name}...`);
      break;

    case 'generation_cancelled':
      if (msg) {
        msg.streaming = false;
        if (!msg.content) msg.content = '(cancelled)';
      }
      state.isStreaming = false;
      document.getElementById('sendBtn').style.display = 'flex';
      document.getElementById('stopBtn').style.display = 'none';
      updateThinkingStatus(null);
      renderMessages();
      showStatus('generation cancelled');
      break;

    case 'turn_complete':
      if (msg) msg.streaming = false;
      state.isStreaming = false;
      document.getElementById('sendBtn').style.display = 'flex';
      document.getElementById('stopBtn').style.display = 'none';
      updateThinkingStatus(null);
      if (data.conversation_id && conv) {
        conv.serverId = data.conversation_id;
      }
      // Update token counter + context bar
      // Use context_tokens (current context window size) for the counter and warning,
      // not cumulative usage across all conversations.
      if (data.context_tokens != null) {
        state.totalTokens = data.context_tokens;
        document.getElementById('tokenCount').textContent = data.context_tokens.toLocaleString();
      } else if (data.usage) {
        // Fallback if server doesn't send context_tokens
        const inTok = data.usage.total_input_tokens || 0;
        const outTok = data.usage.total_output_tokens || 0;
        state.totalTokens = inTok + outTok;
        document.getElementById('tokenCount').textContent = state.totalTokens.toLocaleString();
      }
      if (data.usage) {
        const inTok = data.usage.total_input_tokens || 0;
        const outTok = data.usage.total_output_tokens || 0;
        const costEl = document.getElementById('tokenCounter');
        const costStr = data.usage.total_cost_usd ? `  |  $${data.usage.total_cost_usd.toFixed(4)}` : '';
        costEl.title = `Cumulative: ${inTok.toLocaleString()} in / ${outTok.toLocaleString()} out${costStr}`;
      }
      checkContextUsage();
      renderMessages();
      renderChatList();
      scrollToBottom();
      // Refresh conversation list from server
      fetchConversationHistory();
      // Auto-generate title after first assistant response
      if (conv && conv.serverId && !conv.titleGenerated) {
        const assistantMsgs = conv.messages.filter(m => m.role === 'assistant' && m.content);
        if (assistantMsgs.length === 1) {
          conv.titleGenerated = true;
          sendWsEvent('generate_title', {});
        }
      }
      break;

    case 'conversation_forked': {
      const forkId = 'conv_' + Date.now();
      state.conversations[forkId] = {
        id: forkId,
        serverId: data.conversation_id,
        title: (conv ? conv.title : 'Conversation') + ' (fork)',
        messages: conv ? [...conv.messages.map(m => ({...m, streaming: false}))] : [],
        createdAt: Date.now(),
        forkedFrom: data.source_conversation_id,
      };
      switchChat(forkId);
      renderChatList();
      showStatus('conversation forked');
      break;
    }

    case 'title_updated':
      if (data.title) {
        for (const c of Object.values(state.conversations)) {
          if (c.serverId === data.conversation_id) {
            c.title = data.title;
            if (state.currentId === c.id) {
              document.getElementById('topbarTitle').textContent = data.title;
            }
          }
        }
        renderChatList();
      }
      break;

    case 'context_summarized':
      showStatus(`context reduced: ${data.original_count} msgs -> ${data.new_count} msgs`);
      _contextWarningDismissed = false;
      state.totalTokens = 0;
      updateTokenCounter();
      checkContextUsage();
      break;

    case 'error':
      if (msg) {
        msg.content += '\n\nError: ' + data.message;
        msg.streaming = false;
      }
      state.isStreaming = false;
      document.getElementById('sendBtn').style.display = 'flex';
      document.getElementById('stopBtn').style.display = 'none';
      updateThinkingStatus(null);
      showStatus('error: ' + data.message, 6000);
      renderMessages();
      break;
  }
}

function getStreamingMsg(conv) {
  return conv.messages.find(m => m.streaming);
}

// ═══════════════════════════════════════
// CONVERSATIONS
// ═══════════════════════════════════════
function newChat() {
  const id = 'conv_' + Date.now();
  state.conversations[id] = {
    id,
    title: 'New conversation',
    messages: [],
    createdAt: Date.now(),
  };
  switchChat(id);
  renderChatList();
}

function switchChat(id) {
  state.currentId = id;
  const conv = currentConv();
  document.getElementById('topbarTitle').textContent = conv.title;
  renderMessages();
  renderChatList();
  state.totalTokens = 0;
  updateTokenCounter();
}

function renderChatList() {
  const list = document.getElementById('chatList');
  const searchQuery = (state.convSearchQuery || '').toLowerCase();

  // Current session conversations
  const localConvs = Object.values(state.conversations)
    .filter(c => !searchQuery || c.title.toLowerCase().includes(searchQuery))
    .sort((a, b) => b.createdAt - a.createdAt);

  // Separate pinned from unpinned
  const pinned = localConvs.filter(c => isPinned(c));
  const unpinned = localConvs.filter(c => !isPinned(c));

  const renderItem = (c, isServer) => {
    const pin = isPinned(c);
    const pinBtn = `<button class="chat-item-pin ${pin ? 'pinned' : ''}" data-action="pin" data-chat-id="${c.id}" title="${pin ? 'Unpin' : 'Pin'}">&#9733;</button>`;
    if (isServer) {
      return `<div class="chat-item" data-action="resume-server" data-chat-id="${c.id}">
        <div class="chat-item-dot" style="opacity:0.4"></div>
        <span class="chat-item-title">${escapeHtml(c.title)}</span>
        <button class="chat-item-delete" data-action="delete-server" data-chat-id="${c.id}" title="Delete">&times;</button>
      </div>`;
    }
    return `<div class="chat-item ${c.id === state.currentId ? 'active' : ''}" data-action="switch-chat" data-chat-id="${c.id}">
      ${c.forkedFrom ? '<span class="chat-item-fork-icon">&#9095;</span>' : '<div class="chat-item-dot"></div>'}
      <span class="chat-item-title" data-action="rename-chat" data-chat-id="${c.id}">${escapeHtml(c.title)}</span>
      ${pinBtn}
      <button class="chat-item-delete" data-action="delete-local" data-chat-id="${c.id}" title="Delete">&times;</button>
    </div>`;
  };

  let html = '';
  if (pinned.length > 0) {
    html += '<div class="sidebar-section-label" style="margin-top:0">Pinned</div>';
    html += pinned.map(c => renderItem(c, false)).join('');
    if (unpinned.length > 0) {
      html += '<div class="sidebar-section-label" style="margin-top:8px">Recent</div>';
    }
  }
  html += unpinned.map(c => renderItem(c, false)).join('');

  // Server-saved conversations (exclude ones already loaded locally)
  const loadedServerIds = new Set(
    Object.values(state.conversations).filter(c => c.serverId).map(c => c.serverId)
  );
  const serverConvs = (state.serverConversations || [])
    .filter(c => !loadedServerIds.has(c.id))
    .filter(c => !searchQuery || c.title.toLowerCase().includes(searchQuery));

  if (serverConvs.length > 0) {
    html += '<div class="sidebar-section-label" style="margin-top:12px">History</div>';
    html += serverConvs.map(c => renderItem(c, true)).join('');
  }

  list.innerHTML = html;

  // Event delegation for chat list items
  list.querySelectorAll('[data-action]').forEach(el => {
    const action = el.dataset.action;
    const chatId = el.dataset.chatId;
    if (action === 'switch-chat') {
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-action="pin"]') || e.target.closest('[data-action="delete-local"]')) return;
        switchChat(chatId);
      });
    } else if (action === 'resume-server') {
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-action="delete-server"]')) return;
        resumeServerConversation(chatId);
      });
    } else if (action === 'pin') {
      el.addEventListener('click', (e) => { e.stopPropagation(); togglePin(chatId); });
    } else if (action === 'delete-local') {
      el.addEventListener('click', (e) => { e.stopPropagation(); deleteLocalChat(chatId); });
    } else if (action === 'delete-server') {
      el.addEventListener('click', (e) => { e.stopPropagation(); deleteServerConversation(chatId); });
    } else if (action === 'rename-chat') {
      el.addEventListener('dblclick', (e) => { e.stopPropagation(); renameChat(chatId, el); });
    }
  });
}

let _convSearchTimer = null;
function onConvSearch(value) {
  state.convSearchQuery = value;
  // Debounce server search
  clearTimeout(_convSearchTimer);
  _convSearchTimer = setTimeout(() => {
    if (value.length >= 2) {
      fetchConversationHistory(value);
    } else if (value.length === 0) {
      fetchConversationHistory();
    }
  }, 300);
  renderChatList();
}

async function deleteServerConversation(serverId) {
  const token = localStorage.getItem('lcc_access_token');
  if (!token) return;
  try {
    await fetch(`/api/conversations/${serverId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` },
    });
    // Remove from local state
    state.serverConversations = (state.serverConversations || []).filter(c => c.id !== serverId);
    renderChatList();
  } catch (e) {
    showStatus('failed to delete conversation', 5000);
  }
}

async function fetchConversationHistory(query) {
  const token = localStorage.getItem('lcc_access_token');
  if (!token) return;
  try {
    let url = '/api/conversations';
    if (query) url += `?q=${encodeURIComponent(query)}`;
    const resp = await fetch(url, {
      headers: { 'Authorization': `Bearer ${token}` },
    });
    if (!resp.ok) return;
    const convs = await resp.json();
    state.serverConversations = convs;
    renderChatList();
  } catch (e) {
    // silently ignore
  }
}

function resumeServerConversation(serverId) {
  // Create a local conversation entry and tell the server to load it
  const id = 'conv_' + Date.now();
  const serverConv = (state.serverConversations || []).find(c => c.id === serverId);
  state.conversations[id] = {
    id,
    serverId,
    title: serverConv ? serverConv.title : 'Resumed conversation',
    messages: [],
    createdAt: Date.now(),
  };
  state.currentId = id;
  document.getElementById('topbarTitle').textContent = state.conversations[id].title;
  // Close sidebar on mobile
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarOverlay').classList.remove('visible');
  renderMessages();
  renderChatList();
  sendWsEvent('resume_conversation', { conversation_id: serverId });
}

function populateModelSelector(models, currentModel) {
  const sel = document.getElementById('modelSelector');
  if (!sel) return;
  sel.innerHTML = '';
  models.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m;
    // Friendly label: "claude-sonnet-4-6-20250514" → "Sonnet 4.6"
    const parts = m.split('-');
    let label = m;
    if (parts[0] === 'claude' && parts.length >= 3) {
      const family = parts[1].charAt(0).toUpperCase() + parts[1].slice(1);
      const ver = parts.slice(2).filter(p => !/^\d{8}$/.test(p)).join('.');
      label = family + (ver ? ' ' + ver : '');
    }
    opt.textContent = label;
    sel.appendChild(opt);
  });
  const savedModel = localStorage.getItem('lcc_model');
  // Clear stale model if it's no longer available
  if (savedModel && !models.includes(savedModel)) {
    localStorage.removeItem('lcc_model');
  }
  if (savedModel && models.includes(savedModel)) {
    sel.value = savedModel;
    // Tell server to use the saved model if it differs from current
    if (savedModel !== currentModel) {
      sendWsEvent('set_model', { model: savedModel });
    }
  } else if (currentModel) {
    sel.value = currentModel;
  }
}

function onModelChange(model) {
  if (model) {
    sendWsEvent('set_model', { model });
    localStorage.setItem('lcc_model', model);
  }
}

// ═══════════════════════════════════════
// FILE BROWSER
// ═══════════════════════════════════════
let _fileBrowserPath = '';

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  sidebar.classList.toggle('open');
  overlay.classList.toggle('visible');
}

function collapseSidebar() {
  const sidebar = document.getElementById('sidebar');
  const app = document.getElementById('appShell');
  const isCollapsed = sidebar.classList.toggle('collapsed');
  app.classList.toggle('sidebar-hidden', isCollapsed);
  localStorage.setItem('lcc_sidebar_collapsed', isCollapsed ? '1' : '');
}

// Restore sidebar state on load
(function restoreSidebarState() {
  if (localStorage.getItem('lcc_sidebar_collapsed') === '1') {
    document.getElementById('sidebar')?.classList.add('collapsed');
    document.getElementById('appShell')?.classList.add('sidebar-hidden');
  }
})();

function toggleFilePanel() {
  const panel = document.getElementById('filePanel');
  const isOpen = panel.classList.toggle('open');
  if (isOpen) refreshFiles();
}

async function navigateFiles(path) {
  _fileBrowserPath = path;
  await refreshFiles();
}

async function refreshFiles() {
  const token = localStorage.getItem('lcc_access_token');
  if (!token) return;

  const list = document.getElementById('fileList');
  list.innerHTML = '<div class="file-panel-empty">Loading...</div>';

  try {
    const resp = await fetch(`/api/files/list?path=${encodeURIComponent(_fileBrowserPath)}`, {
      headers: { 'Authorization': `Bearer ${token}` },
    });
    if (!resp.ok) throw new Error(await resp.text());
    const entries = await resp.json();

    // Update breadcrumb
    updateBreadcrumb(_fileBrowserPath);

    if (entries.length === 0) {
      list.innerHTML = '<div class="file-panel-empty">Empty directory</div>';
      return;
    }

    list.innerHTML = '';
    // Add parent directory entry if not at root
    if (_fileBrowserPath) {
      const parent = _fileBrowserPath.split('/').slice(0, -1).join('/');
      const el = document.createElement('div');
      el.className = 'file-entry';
      el.innerHTML = '<span class="file-entry-icon">..</span><span class="file-entry-name">..</span>';
      el.onclick = () => navigateFiles(parent);
      list.appendChild(el);
    }

    entries.forEach(entry => {
      const el = document.createElement('div');
      el.className = 'file-entry';
      const icon = entry.is_dir ? '\u{1F4C1}' : '\u{1F4C4}';
      const size = entry.size != null ? formatFileSize(entry.size) : '';
      el.innerHTML = `<span class="file-entry-icon">${entry.is_dir ? '&#x25B6;' : '&#x25AA;'}</span>` +
        `<span class="file-entry-name">${escapeHtml(entry.name)}</span>` +
        `<span class="file-entry-size">${size}</span>`;
      if (entry.is_dir) {
        el.onclick = () => navigateFiles(entry.path);
      } else {
        el.onclick = () => previewFile(entry.path, entry.name);
      }
      list.appendChild(el);
    });
  } catch (e) {
    list.innerHTML = `<div class="file-panel-empty">Error: ${escapeHtml(e.message)}</div>`;
  }
}

function updateBreadcrumb(path) {
  const bc = document.getElementById('fileBreadcrumb');
  let html = '<span data-path="">workspace</span>';
  if (path) {
    const parts = path.split('/');
    let cumulative = '';
    parts.forEach((part, i) => {
      cumulative += (i > 0 ? '/' : '') + part;
      html += ` / <span data-path="${cumulative}">${escapeHtml(part)}</span>`;
    });
  }
  bc.innerHTML = html;
  bc.querySelectorAll('span[data-path]').forEach(span => {
    span.addEventListener('click', () => navigateFiles(span.dataset.path));
  });
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function previewFile(path, name) {
  const token = localStorage.getItem('lcc_access_token');
  try {
    const resp = await fetch(`/api/files/read?path=${encodeURIComponent(path)}`, {
      headers: { 'Authorization': `Bearer ${token}` },
    });
    if (!resp.ok) {
      // If binary, offer download
      if (resp.status === 400) {
        downloadFile(path);
        return;
      }
      throw new Error(await resp.text());
    }
    const data = await resp.json();
    // Insert content as a message in the chat
    const preview = data.content.length > 5000
      ? data.content.substring(0, 5000) + '\n\n... (truncated)'
      : data.content;
    // Insert file content as a reference message in the input
    const textarea = document.getElementById('inputTextarea');
    textarea.value += (textarea.value ? '\n' : '') + `[File: ${name}]\n\`\`\`\n${preview}\n\`\`\``;
    autoResize(textarea);
    showToast(`${name} (${formatFileSize(data.size)}) inserted`);
    navigator.clipboard.writeText(data.content).catch(() => {});
  } catch (e) {
    showToast(`Cannot read: ${e.message}`);
  }
}

function downloadFile(path) {
  const token = localStorage.getItem('lcc_access_token');
  // Create a temporary link to trigger download
  const a = document.createElement('a');
  a.href = `/api/files/download?path=${encodeURIComponent(path)}&token=${encodeURIComponent(token)}`;
  a.download = path.split('/').pop() || 'file';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function uploadFile() {
  const input = document.createElement('input');
  input.type = 'file';
  input.onchange = async () => {
    if (!input.files || !input.files[0]) return;
    const token = localStorage.getItem('lcc_access_token');
    const formData = new FormData();
    formData.append('file', input.files[0]);
    try {
      const resp = await fetch(`/api/files/upload?path=${encodeURIComponent(_fileBrowserPath)}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
        body: formData,
      });
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      showToast(`Uploaded ${data.path}`);
      refreshFiles();
    } catch (e) {
      showToast(`Upload failed: ${e.message}`);
    }
  };
  input.click();
}

function logout() {
  localStorage.removeItem('lcc_access_token');
  localStorage.removeItem('lcc_refresh_token');
  localStorage.removeItem('lcc_user');
  window.location.href = '/login';
}

// ═══════════════════════════════════════
// SYSTEM PROMPT
// ═══════════════════════════════════════
function toggleSysPrompt() {
  document.getElementById('syspromptBar').classList.toggle('expanded');
}

function handleSysPromptChange() {
  const val = document.getElementById('syspromptTextarea').value.trim();
  const ind = document.getElementById('syspromptIndicator');
  ind.classList.toggle('has-content', val.length > 0);
  sendWsEvent('set_system_prompt', { text: val });
}

// ═══════════════════════════════════════
// INPUT
// ═══════════════════════════════════════
function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

function handleKeydown(e) {
  if (handleAutocompleteKey(e)) return;
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function sendSuggestion(text) {
  document.getElementById('inputTextarea').value = text;
  sendMessage();
}

// ═══════════════════════════════════════
// SEND MESSAGE
// ═══════════════════════════════════════
function sendMessage() {
  if (state.isStreaming) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    showStatus('not connected to server', 6000);
    return;
  }

  const textarea = document.getElementById('inputTextarea');
  const text = textarea.value.trim();
  if (!text) return;

  textarea.value = '';
  textarea.style.height = 'auto';

  const conv = currentConv();
  conv.messages.push({ role: 'user', content: text, id: 'u_' + Date.now(), timestamp: Date.now() });

  if (conv.title === 'New conversation' && conv.messages.length === 1) {
    conv.title = text.slice(0, 40) + (text.length > 40 ? '...' : '');
    document.getElementById('topbarTitle').textContent = conv.title;
    renderChatList();
  }

  // Add placeholder assistant message for streaming
  const assistantMsgId = 'msg_' + Date.now();
  conv.messages.push({
    role: 'assistant',
    content: '',
    id: assistantMsgId,
    toolCalls: [],
    streaming: true,
    timestamp: Date.now(),
  });

  state.isStreaming = true;
  document.getElementById('sendBtn').style.display = 'none';
  document.getElementById('stopBtn').style.display = 'flex';
  updateThinkingStatus('Generating...');

  renderMessages();
  scrollToBottom();

  sendWsEvent('user_message', { text });
}

function cancelGeneration() {
  sendWsEvent('cancel_generation', {});
}

// ═══════════════════════════════════════
// PERMISSION DIALOG
// ═══════════════════════════════════════
function showPermissionDialog(requestId, toolName, summary) {
  const overlay = document.getElementById('permissionOverlay');
  document.getElementById('permissionBody').innerHTML =
    '<strong>' + escapeHtml(toolName) + '</strong> wants to run: ' + escapeHtml(summary);
  overlay.classList.add('visible');
  document.getElementById('permDenyBtn').focus();

  document.getElementById('permAllowBtn').onclick = () => {
    sendWsEvent('permission_response', { request_id: requestId, allowed: true });
    overlay.classList.remove('visible');
  };
  document.getElementById('permDenyBtn').onclick = () => {
    sendWsEvent('permission_response', { request_id: requestId, allowed: false });
    overlay.classList.remove('visible');
  };
}

// ═══════════════════════════════════════
// RENDERING
// ═══════════════════════════════════════
function renderMessages() {
  const container = document.getElementById('messages');
  const conv = currentConv();

  if (!conv || conv.messages.length === 0) {
    container.innerHTML = `<div class="empty-state">
      <div class="empty-logo"><svg width="28" height="28" viewBox="0 0 32 32" fill="none"><g transform="translate(16,16) scale(1.3) translate(-16,-16)"><path d="M24 4c-3 2-6 5-8 9s-3 8-3.5 11c-.1.8-.2 1.5-.2 2l-.3.5c-.5-.5-1.2-1.5-1.5-3-.4-1.8-.2-4 1-6.5 1.5-3 3-5.5 5-7.5s4-3.5 6-4.5c.5-.2.9-.4 1.2-.5L24 4z" fill="#fff" opacity=".5"/><path d="M24 4c-2 1-4 2.5-6 4.5s-3.5 4.5-5 7.5c-1.2 2.5-1.4 4.7-1 6.5.3 1.5 1 2.5 1.5 3l.3-.5c0-.5.1-1.2.2-2 .5-3 1.5-7 3.5-11s5-7 8-9l.2-.1-.5.1c-.3.1-.7.3-1.2.5z" fill="#fff"/><line x1="12.5" y1="25.5" x2="8" y2="28" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/></g></svg></div>
      <h2>Start a conversation</h2>
      <p>An AI assistant with tools for code execution, data analysis, and visualization.</p>
      <div class="suggestions-grid">
        <div class="suggestion-chip" data-suggestion="Plot a sine wave">Plot a sine wave</div>
        <div class="suggestion-chip" data-suggestion="Write a Python function to detect anomalies in a time series using z-scores">Anomaly detection in Python</div>
        <div class="suggestion-chip" data-suggestion="List all files in the current directory">List files</div>
        <div class="suggestion-chip" data-suggestion="Explain how SOFR curves are constructed from futures and swap instruments">Explain SOFR curve construction</div>
      </div>
    </div>`;
    container.querySelectorAll('.suggestion-chip').forEach(chip => {
      chip.addEventListener('click', () => sendSuggestion(chip.dataset.suggestion));
    });
    return;
  }

  container.innerHTML = conv.messages.map(msg => renderMessageHTML(msg)).join('');
  addCopyButtonListeners();
  bindMessageActions();
  // Re-render Plotly charts and HTML embeds after full DOM rebuild
  for (const msg of conv.messages) {
    if (msg.toolCalls) {
      for (const tc of msg.toolCalls) {
        if (tc.chart) {
          setTimeout(() => renderPlotlyChart('chart-' + tc.id, tc.chart.plotlyJson), 20);
        }
        if (tc.embeds) {
          setTimeout(() => hydrateEmbeds(tc.id, tc.embeds), 20);
        }
      }
    }
  }
  postRenderHighlight();
}

function renderStreamingMessage(msg) {
  const el = document.getElementById('msg-' + msg.id);
  if (el) {
    el.outerHTML = renderMessageHTML(msg);
    addCopyButtonListeners();
    bindMessageActions();
  } else {
    renderMessages();
  }
  // Re-render Plotly charts and HTML embeds (outerHTML replacement destroys them)
  if (msg.toolCalls) {
    for (const tc of msg.toolCalls) {
      if (tc.chart) {
        setTimeout(() => renderPlotlyChart('chart-' + tc.id, tc.chart.plotlyJson), 20);
      }
      if (tc.embeds) {
        setTimeout(() => hydrateEmbeds(tc.id, tc.embeds), 20);
      }
    }
  }
  postRenderHighlight();
  scrollToBottom();
}

function formatMsgTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function renderMessageHTML(msg) {
  const timeHTML = msg.timestamp ? `<span class="msg-time">${formatMsgTime(msg.timestamp)}</span>` : '';

  if (msg.role === 'user') {
    return `<div class="msg-row user-row" id="msg-${msg.id}" data-msg-id="${msg.id}">
      <div class="msg-avatar user-av">U</div>
      <div class="msg-body">
        <div class="msg-header">
          <span class="msg-role user">You</span>
          ${timeHTML}
          <div class="msg-actions">
            <button class="msg-action-btn" data-action="edit" data-msg-id="${msg.id}">edit</button>
            <button class="msg-action-btn" data-action="copy" data-msg-id="${msg.id}">copy</button>
          </div>
        </div>
        <div class="msg-prose" id="prose-${msg.id}">${escapeHtml(msg.content).replace(/\n/g, '<br>')}</div>
      </div>
    </div>`;
  }

  // Assistant
  const toolCallsHTML = msg.toolCalls && msg.toolCalls.length > 0
    ? `<div class="tool-calls-container">${msg.toolCalls.map(tc => renderToolCall(tc)).join('')}</div>`
    : '';

  // Collect inline media (images, charts, tables) from all tool calls — render in main chat flow
  let inlineMediaHTML = '';
  if (msg.toolCalls) {
    const mediaItems = [];
    for (const tc of msg.toolCalls) {
      if (tc.tables) {
        for (const tableHtml of tc.tables) {
          mediaItems.push(`<div class="inline-media-item table-item">${tableHtml}</div>`);
        }
      }
      if (tc.images) {
        for (const img of tc.images) {
          mediaItems.push(`<div class="inline-media-item"><img src="data:${img.mime};base64,${img.data}" alt="${escapeAttr(img.name)}"></div>`);
        }
      }
      if (tc.chart) {
        mediaItems.push(`<div class="inline-media-item"><div class="chart-container" id="chart-${tc.id}"></div></div>`);
      }
      if (tc.embeds) {
        for (let i = 0; i < tc.embeds.length; i++) {
          mediaItems.push(`<div class="inline-media-item"><iframe class="html-embed" data-embed-tool="${tc.id}" data-embed-idx="${i}" sandbox="allow-scripts allow-same-origin" title="${escapeAttr(tc.embeds[i].name)}"></iframe></div>`);
        }
      }
    }
    if (mediaItems.length > 0) {
      inlineMediaHTML = `<div class="inline-media-container">${mediaItems.join('')}</div>`;
    }
  }

  const contentHTML = msg.content
    ? renderMarkdown(msg.content)
    : (msg.streaming ? `<div class="streaming-indicator"><div class="stream-dots"><span></span><span></span><span></span></div><span>Generating...</span></div>` : '');

  const cursorHTML = msg.streaming && msg.content ? '<span class="cursor-blink"></span>' : '';

  return `<div class="msg-row assistant-row" id="msg-${msg.id}" data-msg-id="${msg.id}">
    <div class="msg-avatar ai-av"><svg width="14" height="14" viewBox="0 0 32 32" fill="none"><g transform="translate(16,16) scale(1.3) translate(-16,-16)"><path d="M24 4c-3 2-6 5-8 9s-3 8-3.5 11c-.1.8-.2 1.5-.2 2l-.3.5c-.5-.5-1.2-1.5-1.5-3-.4-1.8-.2-4 1-6.5 1.5-3 3-5.5 5-7.5s4-3.5 6-4.5c.5-.2.9-.4 1.2-.5L24 4z" fill="#fff" opacity=".5"/><path d="M24 4c-2 1-4 2.5-6 4.5s-3.5 4.5-5 7.5c-1.2 2.5-1.4 4.7-1 6.5.3 1.5 1 2.5 1.5 3l.3-.5c0-.5.1-1.2.2-2 .5-3 1.5-7 3.5-11s5-7 8-9l.2-.1-.5.1c-.3.1-.7.3-1.2.5z" fill="#fff"/><line x1="12.5" y1="25.5" x2="8" y2="28" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/></g></svg></div>
    <div class="msg-body">
      <div class="msg-header">
        <span class="msg-role assistant">Light CC</span>
        ${timeHTML}
        <div class="msg-actions">
          ${!msg.streaming ? `<button class="msg-action-btn retry" data-action="retry" data-msg-id="${msg.id}">retry</button>` : ''}
          <button class="msg-action-btn" data-action="fork">fork</button>
          ${msg.content ? `<button class="msg-action-btn" data-action="copy" data-msg-id="${msg.id}">copy</button>` : ''}
        </div>
      </div>
      ${toolCallsHTML}
      ${inlineMediaHTML}
      <div class="msg-prose">${contentHTML}${cursorHTML}</div>
    </div>
  </div>`;
}

function renderToolCall(tc) {
  const statusIcon = tc.status === 'running'
    ? `<div class="tool-status-icon running">&#8635;</div>`
    : tc.status === 'error'
    ? `<div class="tool-status-icon error">&#10005;</div>`
    : `<div class="tool-status-icon done">&#10003;</div>`;

  const badge = getToolBadge(tc.name);
  const duration = tc.duration ? `<span class="tool-duration">${tc.duration}s</span>` : '';

  const inputStr = typeof tc.input === 'object'
    ? JSON.stringify(tc.input, null, 2)
    : '';

  const resultText = tc.result
    ? (typeof tc.result === 'string' ? tc.result : JSON.stringify(tc.result, null, 2))
    : '';
  const resultHTML = tc.result
    ? `<div class="tool-section">
        <div class="tool-section-label"><span>Output</span><button class="tool-copy-btn" data-tool-result="${tc.id}">copy</button></div>
        <div class="tool-code ${tc.status === 'error' ? 'tool-result-err' : 'tool-result-ok'}">${escapeHtml(resultText)}</div>
      </div>`
    : '';

  return `<div class="tool-block" id="tool-${tc.id}">
    <div class="tool-header" data-action="toggle-tool" data-tool-id="tool-${tc.id}">
      ${statusIcon}
      <span class="tool-name">${escapeHtml(tc.name)}</span>
      <span class="tool-type-badge ${badge.cls}">${badge.label}</span>
      ${duration}
      <span class="tool-chevron">&#9660;</span>
    </div>
    <div class="tool-body">
      ${inputStr ? `<div class="tool-section">
        <div class="tool-section-label">Input</div>
        <div class="tool-code">${escapeHtml(inputStr)}</div>
      </div>` : ''}
      ${resultHTML}
    </div>
  </div>`;
}

function getToolBadge(name) {
  const n = name.toLowerCase();
  if (n === 'bash') return { cls: 'bash', label: 'bash' };
  if (n === 'python_exec') return { cls: 'python', label: 'python' };
  if (n.includes('chart')) return { cls: 'chart', label: 'chart' };
  if (n.includes('read') || n.includes('fetch') || n.includes('get')) return { cls: 'read', label: 'read' };
  if (n.includes('write') || n.includes('create') || n.includes('save') || n.includes('edit')) return { cls: 'write', label: 'write' };
  if (n.includes('search') || n.includes('find') || n.includes('query') || n.includes('grep') || n.includes('glob')) return { cls: 'search', label: 'search' };
  return { cls: 'generic', label: 'tool' };
}

// ═══════════════════════════════════════
// MARKDOWN RENDERER (marked.js + Prism.js + KaTeX)
// ═══════════════════════════════════════
marked.use({
  gfm: true,
  breaks: true,
  renderer: {
    code({ text, lang }) {
      const language = lang || 'code';
      const langClass = lang ? ` class="language-${lang}"` : '';
      return `<pre><div class="code-block-header"><span>${escapeHtml(language)}</span><button class="copy-btn" data-code="${escapeAttr(text)}">copy</button></div><code${langClass}>${escapeHtml(text)}</code></pre>`;
    },
    table(token) {
      const align = token.align || [];
      let thead = '<thead><tr>';
      for (let i = 0; i < token.header.length; i++) {
        const cell = token.header[i];
        const cls = align[i] === 'right' ? ' class="num"' : '';
        const cellText = this.parser.parseInline(cell.tokens);
        thead += `<th${cls}>${cellText}</th>`;
      }
      thead += '</tr></thead>';
      let tbody = '<tbody>';
      for (const row of token.rows) {
        tbody += '<tr>';
        for (let i = 0; i < row.length; i++) {
          const cell = row[i];
          const cls = align[i] === 'right' ? ' class="num"' : '';
          const cellText = this.parser.parseInline(cell.tokens);
          tbody += `<td${cls}>${cellText}</td>`;
        }
        tbody += '</tr>';
      }
      tbody += '</tbody>';
      return `<div class="inline-media-item table-item"><table class="data-table">${thead}${tbody}</table></div>`;
    },
    link({ href, title, tokens }) {
      const text = this.parser.parseInline(tokens);
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
      return `<a href="${escapeHtml(href)}"${titleAttr} target="_blank" rel="noopener">${text}</a>`;
    },
    blockquote({ tokens }) {
      const body = this.parser.parse(tokens);
      return `<blockquote>${body}</blockquote>`;
    },
  },
});

function renderMarkdown(text) {
  const mathBlocks = [];
  let processed = text;

  // Extract display math: $$...$$ and \[...\]
  processed = processed.replace(/\$\$([\s\S]*?)\$\$/g, (_, tex) => {
    const id = `MATHD${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: true });
    return id;
  });
  processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, (_, tex) => {
    const id = `MATHD${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: true });
    return id;
  });

  // Extract inline math: $...$ and \(...\)
  processed = processed.replace(/\$([^\$\n]+?)\$/g, (_, tex) => {
    const id = `MATHI${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: false });
    return id;
  });
  processed = processed.replace(/\\\(([^)]+?)\\\)/g, (_, tex) => {
    const id = `MATHI${mathBlocks.length}MATHEND`;
    mathBlocks.push({ tex: tex.trim(), display: false });
    return id;
  });

  // Parse with marked
  let html = marked.parse(processed);

  // Sanitize
  if (typeof DOMPurify !== 'undefined') {
    html = DOMPurify.sanitize(html, {
      ADD_TAGS: ['iframe'],
      ADD_ATTR: ['target', 'rel', 'data-code', 'data-embed-tool', 'data-embed-idx', 'sandbox', 'class'],
    });
  }

  // Restore math blocks with KaTeX
  for (let i = 0; i < mathBlocks.length; i++) {
    const { tex, display } = mathBlocks[i];
    const placeholder = display ? `MATHD${i}MATHEND` : `MATHI${i}MATHEND`;
    let rendered;
    if (typeof katex !== 'undefined') {
      try {
        rendered = katex.renderToString(tex, { displayMode: display, throwOnError: false, trust: true });
        rendered = display
          ? `<div class="math-display">${rendered}</div>`
          : `<span class="math-inline">${rendered}</span>`;
      } catch (e) {
        rendered = display
          ? `<div class="math-display"><code>${escapeHtml(tex)}</code></div>`
          : `<code>${escapeHtml(tex)}</code>`;
      }
    } else {
      rendered = display
        ? `<div class="math-display"><code>${escapeHtml(tex)}</code></div>`
        : `<code>${escapeHtml(tex)}</code>`;
    }
    html = html.replace(placeholder, rendered);
  }

  return html;
}

function postRenderHighlight() {
  if (typeof Prism !== 'undefined') {
    Prism.highlightAllUnder(document.getElementById('messages'));
  }
}

// ═══════════════════════════════════════
// CONVERSATION FORKING
// ═══════════════════════════════════════
function forkFromMessage() {
  const conv = currentConv();
  if (!conv || !conv.serverId) {
    showStatus('conversation must be saved first');
    return;
  }
  sendWsEvent('fork_conversation', { conversation_id: conv.serverId });
}

// ═══════════════════════════════════════
// MESSAGE EDITING
// ═══════════════════════════════════════
function editMessage(msgId) {
  const conv = currentConv();
  if (!conv) return;
  const msg = conv.messages.find(m => m.id === msgId);
  if (!msg || msg.role !== 'user') return;

  const proseEl = document.getElementById('prose-' + msgId);
  if (!proseEl) return;

  proseEl.innerHTML = `<textarea class="msg-edit-area" id="edit-textarea-${msgId}">${escapeHtml(msg.content)}</textarea>
    <div class="msg-edit-actions">
      <button class="edit-cancel" data-action="cancel-edit">Cancel</button>
      <button class="edit-save" data-action="submit-edit" data-msg-id="${msgId}">Save & Resend</button>
    </div>`;

  const textarea = document.getElementById('edit-textarea-' + msgId);
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);
}

function cancelEdit() {
  renderMessages();
}

function submitEdit(msgId) {
  const conv = currentConv();
  if (!conv || state.isStreaming) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    showStatus('not connected to server', 6000);
    return;
  }

  const textarea = document.getElementById('edit-textarea-' + msgId);
  if (!textarea) return;
  const newText = textarea.value.trim();
  if (!newText) return;

  // Find the message index and truncate everything from it onward
  const idx = conv.messages.findIndex(m => m.id === msgId);
  if (idx === -1) return;
  conv.messages = conv.messages.slice(0, idx);

  // Reset server-side conversation
  sendWsEvent('clear_conversation', {});

  // Push edited message + streaming placeholder
  conv.messages.push({ role: 'user', content: newText, id: 'u_' + Date.now(), timestamp: Date.now() });
  conv.messages.push({
    role: 'assistant', content: '', id: 'msg_' + Date.now(),
    toolCalls: [], streaming: true, timestamp: Date.now(),
  });

  state.isStreaming = true;
  document.getElementById('sendBtn').style.display = 'none';
  document.getElementById('stopBtn').style.display = 'flex';
  updateThinkingStatus('Generating...');

  renderMessages();
  scrollToBottom();
  sendWsEvent('user_message', { text: newText });
}

// ═══════════════════════════════════════
// TOGGLES & UTILS
// ═══════════════════════════════════════
function toggleToolBlock(id) {
  document.getElementById(id)?.classList.toggle('expanded');
}

function bindMessageActions() {
  const container = document.getElementById('messages');
  container.querySelectorAll('[data-action]').forEach(el => {
    const action = el.dataset.action;
    const msgId = el.dataset.msgId;
    if (action === 'edit') {
      el.addEventListener('click', () => editMessage(msgId));
    } else if (action === 'copy') {
      el.addEventListener('click', () => copyAssistantMessage(msgId));
    } else if (action === 'retry') {
      el.addEventListener('click', () => retryLastMessage(msgId));
    } else if (action === 'fork') {
      el.addEventListener('click', () => forkFromMessage());
    } else if (action === 'toggle-tool') {
      el.addEventListener('click', () => toggleToolBlock(el.dataset.toolId));
    } else if (action === 'cancel-edit') {
      el.addEventListener('click', () => cancelEdit());
    } else if (action === 'submit-edit') {
      el.addEventListener('click', () => submitEdit(msgId));
    }
  });
}

function addCopyButtonListeners() {
  document.querySelectorAll('.copy-btn[data-code]').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(btn.dataset.code).then(() => {
        btn.textContent = 'copied!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('copied'); }, 1500);
      });
    };
  });
  // Tool result copy buttons
  document.querySelectorAll('.tool-copy-btn[data-tool-result]').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const toolId = btn.dataset.toolResult;
      const codeEl = btn.closest('.tool-section')?.querySelector('.tool-code');
      if (codeEl) {
        navigator.clipboard.writeText(codeEl.textContent).then(() => {
          btn.textContent = 'copied!';
          btn.classList.add('copied');
          setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('copied'); }, 1500);
        });
      }
    };
  });
}

function scrollToBottom() {
  const el = document.getElementById('messages');
  el.scrollTop = el.scrollHeight;
}

function updateTokenCounter() {
  document.getElementById('tokenCount').textContent = state.totalTokens.toLocaleString();
}

function clearChat() {
  if (!currentConv()) return;
  currentConv().messages = [];
  state.totalTokens = 0;
  updateTokenCounter();
  renderMessages();
  sendWsEvent('clear_conversation', {});
}

function exportConversation() {
  const conv = currentConv();
  if (!conv || conv.messages.length === 0) { showStatus('nothing to export'); return; }

  const md = conv.messages.map(m =>
    `## ${m.role === 'user' ? 'You' : 'Light CC'}\n\n${m.content}`
  ).join('\n\n---\n\n');

  const blob = new Blob([md], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = conv.title.replace(/[^a-z0-9]/gi, '_') + '.md';
  a.click();
  showToast('Exported as markdown');
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => showToast('Copied to clipboard'));
}

function copyAssistantMessage(msgId) {
  const conv = currentConv();
  if (!conv) return;
  const msg = conv.messages.find(m => m.id === msgId);
  if (!msg || !msg.content) return;
  const btn = document.querySelector(`[data-msg-id="${msgId}"] .msg-action-btn:last-child`);
  navigator.clipboard.writeText(msg.content).then(() => {
    showToast('Copied to clipboard');
    if (btn) {
      btn.textContent = 'copied!';
      btn.classList.add('copied-feedback');
      setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('copied-feedback'); }, 1500);
    }
  });
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

let _statusTimer = null;
function showStatus(msg, duration) {
  const el = document.getElementById('statusLine');
  el.textContent = msg;
  el.classList.add('visible');
  clearTimeout(_statusTimer);
  if (duration !== 0) {
    _statusTimer = setTimeout(() => el.classList.remove('visible'), duration || 4000);
  }
}

function clearStatus() {
  const el = document.getElementById('statusLine');
  el.classList.remove('visible');
  clearTimeout(_statusTimer);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeAttr(str) {
  return String(str).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ═══════════════════════════════════════
// SLASH COMMAND AUTOCOMPLETE
// ═══════════════════════════════════════
let _acIndex = -1;
let _acVisible = false;

function updateAutocomplete(textarea) {
  const text = textarea.value;
  const dropdown = document.getElementById('autocompleteDropdown');
  const skills = state.skills || [];

  // Only activate when text starts with / and cursor is in the first word
  const cursorPos = textarea.selectionStart;
  const textBeforeCursor = text.substring(0, cursorPos);

  if (!textBeforeCursor.startsWith('/') || textBeforeCursor.includes(' ') || skills.length === 0) {
    hideAutocomplete();
    return;
  }

  const query = textBeforeCursor.substring(1).toLowerCase();
  const matches = skills.filter(s => s.name.toLowerCase().startsWith(query));

  if (matches.length === 0) {
    hideAutocomplete();
    return;
  }

  _acIndex = 0;
  _acVisible = true;
  dropdown.innerHTML = matches.map((s, i) =>
    `<div class="autocomplete-item${i === 0 ? ' active' : ''}" data-name="${escapeHtml(s.name)}" onmousedown="selectAutocomplete('${escapeAttr(s.name)}')">
      <div><span class="autocomplete-name">/${escapeHtml(s.name)}</span>${s.argument_hint ? `<span class="autocomplete-hint">${escapeHtml(s.argument_hint)}</span>` : ''}</div>
      ${s.description ? `<div class="autocomplete-desc">${escapeHtml(s.description)}</div>` : ''}
    </div>`
  ).join('');
  dropdown.classList.add('visible');
}

function hideAutocomplete() {
  _acVisible = false;
  _acIndex = -1;
  document.getElementById('autocompleteDropdown').classList.remove('visible');
}

function selectAutocomplete(name) {
  const textarea = document.getElementById('inputTextarea');
  textarea.value = '/' + name + ' ';
  textarea.focus();
  hideAutocomplete();
}

function handleAutocompleteKey(e) {
  if (!_acVisible) return false;
  const dropdown = document.getElementById('autocompleteDropdown');
  const items = dropdown.querySelectorAll('.autocomplete-item');
  if (items.length === 0) return false;

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    items[_acIndex]?.classList.remove('active');
    _acIndex = (_acIndex + 1) % items.length;
    items[_acIndex]?.classList.add('active');
    items[_acIndex]?.scrollIntoView({ block: 'nearest' });
    return true;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    items[_acIndex]?.classList.remove('active');
    _acIndex = (_acIndex - 1 + items.length) % items.length;
    items[_acIndex]?.classList.add('active');
    items[_acIndex]?.scrollIntoView({ block: 'nearest' });
    return true;
  }
  if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
    e.preventDefault();
    const name = items[_acIndex]?.dataset.name;
    if (name) selectAutocomplete(name);
    return true;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    hideAutocomplete();
    return true;
  }
  return false;
}

// ═══════════════════════════════════════
// DRAG-AND-DROP FILE UPLOAD
// ═══════════════════════════════════════
(function initDragDrop() {
  const wrapper = document.querySelector('.input-wrapper');
  if (!wrapper) return;
  let dragCounter = 0;

  wrapper.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragCounter++;
    wrapper.classList.add('drag-over');
  });
  wrapper.addEventListener('dragover', (e) => e.preventDefault());
  wrapper.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) { dragCounter = 0; wrapper.classList.remove('drag-over'); }
  });
  wrapper.addEventListener('drop', async (e) => {
    e.preventDefault();
    dragCounter = 0;
    wrapper.classList.remove('drag-over');
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) return;
    const token = localStorage.getItem('lcc_access_token');
    if (!token) return;
    const textarea = document.getElementById('inputTextarea');
    for (const file of files) {
      const formData = new FormData();
      formData.append('file', file);
      try {
        const resp = await fetch(`/api/files/upload?path=`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` },
          body: formData,
        });
        if (!resp.ok) throw new Error(await resp.text());
        const data = await resp.json();
        textarea.value += (textarea.value ? '\n' : '') + data.path;
        autoResize(textarea);
        showToast(`Uploaded ${file.name}`);
      } catch (err) {
        showToast(`Upload failed: ${file.name}`);
      }
    }
  });
})();

// ═══════════════════════════════════════
// KEYBOARD SHORTCUTS
// ═══════════════════════════════════════
function toggleShortcuts() {
  document.getElementById('shortcutsOverlay').classList.toggle('visible');
}

document.addEventListener('keydown', (e) => {
  const tag = document.activeElement?.tagName;
  const inInput = tag === 'TEXTAREA' || tag === 'INPUT';

  // Ctrl+F — search in conversation
  if (e.ctrlKey && e.key === 'f') {
    e.preventDefault();
    toggleConvSearch();
    return;
  }

  // Ctrl+B — toggle sidebar
  if (e.ctrlKey && e.key === 'b') {
    e.preventDefault();
    collapseSidebar();
    return;
  }

  // Ctrl+K — new chat
  if (e.ctrlKey && e.key === 'k') {
    e.preventDefault();
    newChat();
    return;
  }

  // Ctrl+/ — toggle shortcuts
  if (e.ctrlKey && e.key === '/') {
    e.preventDefault();
    toggleShortcuts();
    return;
  }

  // Ctrl+Shift+Backspace — clear chat
  if (e.ctrlKey && e.shiftKey && e.key === 'Backspace') {
    e.preventDefault();
    clearChat();
    return;
  }

  // Escape — close overlays/panels
  if (e.key === 'Escape') {
    const searchBar = document.getElementById('convSearchBar');
    if (searchBar.classList.contains('visible')) { closeConvSearch(); return; }
    const shortcuts = document.getElementById('shortcutsOverlay');
    if (shortcuts.classList.contains('visible')) { shortcuts.classList.remove('visible'); return; }
    const perm = document.getElementById('permissionOverlay');
    if (perm.classList.contains('visible')) return; // don't dismiss permission
    const fp = document.getElementById('filePanel');
    if (fp.classList.contains('open')) { fp.classList.remove('open'); return; }
    const sb = document.getElementById('sidebar');
    if (sb.classList.contains('open')) { toggleSidebar(); return; }
    return;
  }

  // Focus input on printable key when not already in a text field
  if (!inInput && !e.ctrlKey && !e.altKey && !e.metaKey && e.key.length === 1) {
    document.getElementById('inputTextarea').focus();
  }
});

// ═══════════════════════════════════════
// IMPORT CONVERSATION
// ═══════════════════════════════════════
function importConversation() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.md,.txt,.markdown';
  input.multiple = true;
  input.onchange = async () => {
    if (!input.files || input.files.length === 0) return;
    const token = localStorage.getItem('lcc_access_token');
    let imported = 0;
    for (const file of input.files) {
      const formData = new FormData();
      formData.append('file', file);
      try {
        const resp = await fetch('/api/conversations/import', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` },
          body: formData,
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          showToast(`Failed: ${file.name} - ${err.detail || 'unknown error'}`);
          continue;
        }
        imported++;
      } catch (e) {
        showToast(`Failed: ${file.name}`);
      }
    }
    if (imported > 0) {
      showToast(`Imported ${imported} conversation${imported > 1 ? 's' : ''}`);
      fetchConversationHistory();
    }
  };
  input.click();
}

// ═══════════════════════════════════════
// THEME SWITCHING
// ═══════════════════════════════════════
function setTheme(name) {
  if (name === 'midnight') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.setAttribute('data-theme', name);
  }
  // Update active dot
  document.querySelectorAll('.theme-dot').forEach(d => {
    const isActive = d.dataset.theme === name;
    d.classList.toggle('active', isActive);
    d.setAttribute('aria-checked', isActive ? 'true' : 'false');
  });
  // Update Prism theme for light mode
  const prismLink = document.querySelector('link[href*="prism"]');
  if (prismLink) {
    prismLink.href = name === 'light'
      ? 'https://cdn.jsdelivr.net/npm/prismjs@1/themes/prism.min.css'
      : 'https://cdn.jsdelivr.net/npm/prismjs@1/themes/prism-tomorrow.min.css';
  }
  localStorage.setItem('lcc_theme', name);
}

// Restore theme on load
(function restoreTheme() {
  const saved = localStorage.getItem('lcc_theme');
  if (saved && saved !== 'midnight') {
    setTheme(saved);
  }
})();

// ═══════════════════════════════════════
// SCROLL-TO-BOTTOM BUTTON
// ═══════════════════════════════════════
(function initScrollDetection() {
  const messagesEl = document.getElementById('messages');
  const btn = document.getElementById('scrollBottomBtn');
  if (!messagesEl || !btn) return;

  messagesEl.addEventListener('scroll', () => {
    const atBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
    btn.classList.toggle('visible', !atBottom);
  });
})();

// ═══════════════════════════════════════
// CLIPBOARD PASTE (images/files)
// ═══════════════════════════════════════
(function initClipboardPaste() {
  const textarea = document.getElementById('inputTextarea');
  if (!textarea) return;

  textarea.addEventListener('paste', async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    for (const item of items) {
      if (item.kind === 'file') {
        e.preventDefault();
        const file = item.getAsFile();
        if (!file) continue;

        const token = localStorage.getItem('lcc_access_token');
        const formData = new FormData();
        formData.append('file', file);
        try {
          const resp = await fetch('/api/files/upload?path=', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: formData,
          });
          if (!resp.ok) throw new Error(await resp.text());
          const data = await resp.json();
          textarea.value += (textarea.value ? '\n' : '') + data.path;
          autoResize(textarea);
          showToast(`Pasted ${file.name || 'file'}`);
        } catch (err) {
          showToast(`Paste upload failed: ${err.message}`);
        }
        break; // Handle one file at a time
      }
    }
  });
})();

// ═══════════════════════════════════════
// CONVERSATION DELETE (with undo)
// ═══════════════════════════════════════
let _pendingDelete = null;
let _undoTimer = null;

function deleteLocalChat(chatId) {
  const conv = state.conversations[chatId];
  if (!conv) return;

  // Store for undo
  _pendingDelete = { id: chatId, conv: conv };

  // Remove from state
  delete state.conversations[chatId];

  // If we deleted the active chat, switch to another or create new
  if (state.currentId === chatId) {
    const remaining = Object.keys(state.conversations);
    if (remaining.length > 0) {
      switchChat(remaining[0]);
    } else {
      newChat();
    }
  }
  renderChatList();

  // Show undo toast
  const undoToast = document.getElementById('undoToast');
  document.getElementById('undoToastMsg').textContent = `Deleted "${conv.title.slice(0, 30)}"`;
  undoToast.classList.add('show');

  clearTimeout(_undoTimer);
  _undoTimer = setTimeout(() => {
    undoToast.classList.remove('show');
    // Also delete from server if it has a serverId
    if (_pendingDelete && _pendingDelete.conv.serverId) {
      deleteServerConversation(_pendingDelete.conv.serverId);
    }
    _pendingDelete = null;
  }, 5000);
}

function undoDelete() {
  if (!_pendingDelete) return;
  clearTimeout(_undoTimer);
  state.conversations[_pendingDelete.id] = _pendingDelete.conv;
  _pendingDelete = null;
  document.getElementById('undoToast').classList.remove('show');
  renderChatList();
  showToast('Restored');
}

// ═══════════════════════════════════════
// CONVERSATION RENAME
// ═══════════════════════════════════════
function renameChat(chatId, titleEl) {
  const conv = state.conversations[chatId];
  if (!conv) return;

  const input = document.createElement('input');
  input.className = 'chat-item-rename';
  input.value = conv.title;

  const finishRename = () => {
    const newTitle = input.value.trim();
    if (newTitle && newTitle !== conv.title) {
      conv.title = newTitle;
      if (chatId === state.currentId) {
        document.getElementById('topbarTitle').textContent = newTitle;
      }
    }
    renderChatList();
  };

  input.addEventListener('blur', finishRename);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = conv.title; input.blur(); }
  });
  input.addEventListener('click', (e) => e.stopPropagation());

  titleEl.replaceWith(input);
  input.focus();
  input.select();
}

// ═══════════════════════════════════════
// THINKING STATUS
// ═══════════════════════════════════════
function updateThinkingStatus(label) {
  const el = document.getElementById('thinkingStatus');
  const labelEl = document.getElementById('thinkingLabel');
  const hints = document.getElementById('inputHints');
  if (!el) return;
  if (label) {
    labelEl.textContent = label;
    el.classList.add('visible');
    if (hints) hints.style.display = 'none';
  } else {
    el.classList.remove('visible');
    if (hints) hints.style.display = '';
  }
}

// ═══════════════════════════════════════
// SEARCH WITHIN CONVERSATION (Ctrl+F)
// ═══════════════════════════════════════
let _searchMatches = [];
let _searchIndex = -1;

function toggleConvSearch() {
  const bar = document.getElementById('convSearchBar');
  if (bar.classList.contains('visible')) {
    closeConvSearch();
  } else {
    bar.classList.add('visible');
    const input = document.getElementById('convSearchInput');
    input.value = '';
    input.focus();
    _searchMatches = [];
    _searchIndex = -1;
    document.getElementById('convSearchCount').textContent = '';
  }
}

function closeConvSearch() {
  document.getElementById('convSearchBar').classList.remove('visible');
  clearSearchHighlights();
  _searchMatches = [];
  _searchIndex = -1;
}

function onConvSearchInput(query) {
  clearSearchHighlights();
  _searchMatches = [];
  _searchIndex = -1;
  const countEl = document.getElementById('convSearchCount');

  if (!query || query.length < 2) {
    countEl.textContent = '';
    return;
  }

  const container = document.getElementById('messages');
  const proseEls = container.querySelectorAll('.msg-prose');
  const regex = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');

  proseEls.forEach(el => {
    highlightTextNodes(el, regex);
  });

  _searchMatches = Array.from(container.querySelectorAll('.search-highlight'));
  countEl.textContent = _searchMatches.length > 0 ? `${_searchMatches.length} found` : 'no results';

  if (_searchMatches.length > 0) {
    _searchIndex = 0;
    _searchMatches[0].classList.add('active');
    _searchMatches[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function highlightTextNodes(el, regex) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);

  for (const node of textNodes) {
    if (node.parentElement.closest('.search-highlight, pre, code, .tool-code')) continue;
    const text = node.textContent;
    if (!regex.test(text)) continue;
    regex.lastIndex = 0;

    const frag = document.createDocumentFragment();
    let lastIndex = 0;
    let match;
    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        frag.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
      }
      const mark = document.createElement('mark');
      mark.className = 'search-highlight';
      mark.textContent = match[0];
      frag.appendChild(mark);
      lastIndex = regex.lastIndex;
    }
    if (lastIndex < text.length) {
      frag.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
    node.parentNode.replaceChild(frag, node);
  }
}

function clearSearchHighlights() {
  document.querySelectorAll('.search-highlight').forEach(mark => {
    const parent = mark.parentNode;
    parent.replaceChild(document.createTextNode(mark.textContent), mark);
    parent.normalize();
  });
}

function convSearchNext() {
  if (_searchMatches.length === 0) return;
  _searchMatches[_searchIndex]?.classList.remove('active');
  _searchIndex = (_searchIndex + 1) % _searchMatches.length;
  _searchMatches[_searchIndex].classList.add('active');
  _searchMatches[_searchIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
  document.getElementById('convSearchCount').textContent = `${_searchIndex + 1}/${_searchMatches.length}`;
}

function convSearchPrev() {
  if (_searchMatches.length === 0) return;
  _searchMatches[_searchIndex]?.classList.remove('active');
  _searchIndex = (_searchIndex - 1 + _searchMatches.length) % _searchMatches.length;
  _searchMatches[_searchIndex].classList.add('active');
  _searchMatches[_searchIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
  document.getElementById('convSearchCount').textContent = `${_searchIndex + 1}/${_searchMatches.length}`;
}

function onConvSearchKey(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    if (e.shiftKey) convSearchPrev(); else convSearchNext();
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    closeConvSearch();
  }
}

// ═══════════════════════════════════════
// PIN CONVERSATIONS
// ═══════════════════════════════════════
function getPinnedIds() {
  try { return JSON.parse(localStorage.getItem('lcc_pinned') || '[]'); }
  catch { return []; }
}

function setPinnedIds(ids) {
  localStorage.setItem('lcc_pinned', JSON.stringify(ids));
}

function togglePin(chatId, e) {
  if (e) e.stopPropagation();
  const pinned = getPinnedIds();
  const conv = state.conversations[chatId];
  // Use serverId for pinning if available (stable across sessions)
  const pinKey = conv?.serverId || chatId;
  const idx = pinned.indexOf(pinKey);
  if (idx >= 0) {
    pinned.splice(idx, 1);
  } else {
    pinned.unshift(pinKey);
  }
  setPinnedIds(pinned);
  renderChatList();
}

function isPinned(conv) {
  const pinned = getPinnedIds();
  return pinned.includes(conv.serverId || conv.id);
}

// ═══════════════════════════════════════
// RETRY / REGENERATE
// ═══════════════════════════════════════
function retryLastMessage(msgId) {
  const conv = currentConv();
  if (!conv || state.isStreaming) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    showStatus('not connected to server', 6000);
    return;
  }

  // Find the assistant message and the user message before it
  const idx = conv.messages.findIndex(m => m.id === msgId);
  if (idx === -1) return;

  // Find the last user message before this assistant message
  let userText = null;
  for (let i = idx - 1; i >= 0; i--) {
    if (conv.messages[i].role === 'user') {
      userText = conv.messages[i].content;
      break;
    }
  }
  if (!userText) { showStatus('no user message to retry'); return; }

  // Truncate from the assistant message onward
  conv.messages = conv.messages.slice(0, idx);

  // Reset server-side
  sendWsEvent('clear_conversation', {});

  // Add new streaming placeholder
  conv.messages.push({
    role: 'assistant', content: '', id: 'msg_' + Date.now(),
    toolCalls: [], streaming: true, timestamp: Date.now(),
  });

  state.isStreaming = true;
  document.getElementById('sendBtn').style.display = 'none';
  document.getElementById('stopBtn').style.display = 'flex';
  updateThinkingStatus('Regenerating...');

  renderMessages();
  scrollToBottom();
  sendWsEvent('user_message', { text: userText });
}

// ═══════════════════════════════════════
// COMPACT / DETAILED VIEW
// ═══════════════════════════════════════
let _compactView = localStorage.getItem('lcc_compact') === 'true';

function toggleCompactView() {
  _compactView = !_compactView;
  localStorage.setItem('lcc_compact', _compactView);
  applyCompactView();
}

function applyCompactView() {
  const el = document.getElementById('messages');
  const btn = document.getElementById('viewToggle');
  el.classList.toggle('compact-view', _compactView);
  btn.classList.toggle('active', _compactView);
}

// Apply on load
(function() { applyCompactView(); })();

// ═══════════════════════════════════════
// CONTEXT WINDOW MANAGEMENT
// ═══════════════════════════════════════
let _contextWarningDismissed = false;
const CONTEXT_LIMIT = 200000; // approximate token limit

function checkContextUsage() {
  const pct = state.totalTokens / CONTEXT_LIMIT;
  const bar = document.getElementById('contextWarning');
  const text = document.getElementById('contextWarningText');

  if (pct >= 0.8 && !_contextWarningDismissed) {
    const pctStr = Math.round(pct * 100);
    text.textContent = `Context window is ${pctStr}% full (${state.totalTokens.toLocaleString()} / ${CONTEXT_LIMIT.toLocaleString()} tokens). Consider summarizing.`;
    bar.classList.add('visible');
  } else {
    bar.classList.remove('visible');
  }

  // Update context bar
  const barFill = document.querySelector('.context-bar-fill');
  const barContainer = document.querySelector('.context-bar');
  if (barFill && barContainer) {
    const w = Math.min(pct * 100, 100);
    barFill.style.width = w + '%';
    barContainer.classList.toggle('active', w > 0);
    barFill.className = 'context-bar-fill ' + (pct < 0.5 ? 'low' : pct < 0.8 ? 'mid' : 'high');
  }
}

function dismissContextWarning() {
  _contextWarningDismissed = true;
  document.getElementById('contextWarning').classList.remove('visible');
}

function truncateContext() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    showStatus('not connected to server', 6000);
    return;
  }
  sendWsEvent('summarize_context', {});
  dismissContextWarning();
  showStatus('requesting context summarization...');
}

// ═══════════════════════════════════════
// STREAMING TOOL OUTPUT
// ═══════════════════════════════════════
function appendToolStream(toolId, text) {
  const conv = currentConv();
  if (!conv) return;
  const msg = getStreamingMsg(conv);
  if (!msg) return;
  const tc = msg.toolCalls.find(t => t.id === toolId);
  if (!tc) return;
  tc.result = (tc.result || '') + text;
  tc.streaming = true;

  // Direct DOM update for performance (avoid full re-render)
  const toolEl = document.getElementById('tool-' + toolId);
  if (toolEl) {
    const codeEl = toolEl.querySelector('.tool-code');
    if (codeEl) {
      codeEl.textContent = tc.result;
      codeEl.classList.add('streaming');
      codeEl.scrollTop = codeEl.scrollHeight;
    } else {
      // Tool body has no output section yet — expand and create it
      toolEl.classList.add('expanded');
      const body = toolEl.querySelector('.tool-body');
      if (body && !body.querySelector('.tool-result-section')) {
        body.insertAdjacentHTML('beforeend',
          `<div class="tool-section tool-result-section">
            <div class="tool-section-label"><span>Output</span></div>
            <div class="tool-code streaming tool-result-ok">${escapeHtml(tc.result)}</div>
          </div>`);
      }
    }
  }
}

function finalizeToolStream(toolId) {
  const toolEl = document.getElementById('tool-' + toolId);
  if (toolEl) {
    const codeEl = toolEl.querySelector('.tool-code');
    if (codeEl) codeEl.classList.remove('streaming');
  }
}
