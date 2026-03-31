<script>
  import { appState, sortedConversations, newConversation, switchConversation } from '../state.svelte.js';
  import { logout, fetchConversationHistory, deleteServerConversation, importConversation } from '../api.js';
  import { send } from '../ws.js';
  import { THEMES, setTheme } from '../theme.js';
  import { debounce } from '../lib/utils.js';
  import { showToast } from '../state.svelte.js';
  import StatusBar from './StatusBar.svelte';

  let searchQuery = $state('');

  const debouncedServerSearch = debounce((value) => {
    if (value.length >= 2) {
      fetchConversationHistory(value);
    } else if (value.length === 0) {
      fetchConversationHistory();
    }
  }, 300);

  function onSearch(e) {
    searchQuery = e.target.value;
    debouncedServerSearch(searchQuery);
  }

  let filteredLocal = $derived(
    sortedConversations().filter(c =>
      !searchQuery || c.title.toLowerCase().includes(searchQuery.toLowerCase())
    )
  );

  let loadedServerIds = $derived(
    new Set(Object.values(appState.conversations).filter(c => c.serverId).map(c => c.serverId))
  );

  let filteredServer = $derived(
    (appState.serverConversations || [])
      .filter(c => !loadedServerIds.has(c.id))
      .filter(c => !searchQuery || c.title.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  function handleNewChat() {
    newConversation();
    send('clear_conversation', {});
  }

  function handleSwitchChat(id) {
    switchConversation(id);
    const conv = appState.conversations[id];
    if (conv && conv.serverId) {
      send('resume_conversation', { conversation_id: conv.serverId });
    }
  }

  function handleResumeServer(serverId) {
    const id = 'conv_' + Date.now();
    const serverConv = (appState.serverConversations || []).find(c => c.id === serverId);
    appState.conversations[id] = {
      id,
      serverId,
      title: serverConv ? serverConv.title : 'Resumed conversation',
      messages: [],
      createdAt: Date.now(),
    };
    appState.currentId = id;
    send('resume_conversation', { conversation_id: serverId });
  }

  function handleDeleteLocal(id) {
    delete appState.conversations[id];
    if (appState.currentId === id) {
      const remaining = Object.keys(appState.conversations);
      if (remaining.length > 0) {
        switchConversation(remaining[0]);
      } else {
        newConversation();
      }
    }
  }

  function handleDeleteServer(serverId) {
    deleteServerConversation(serverId);
  }

  function exportConversation() {
    const conv = appState.conversations[appState.currentId];
    if (!conv || conv.messages.length === 0) {
      showToast('Nothing to export');
      return;
    }
    const md = conv.messages.map(m =>
      `## ${m.role === 'user' ? 'You' : 'Light CC'}\n\n${m.content}`
    ).join('\n\n---\n\n');
    const blob = new Blob([md], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = conv.title.replace(/[^a-z0-9]/gi, '_') + '.md';
    a.click();
    URL.revokeObjectURL(a.href);
    showToast('Exported as markdown');
  }

  function handleImport() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.md,.txt,.markdown';
    input.multiple = true;
    input.onchange = async () => {
      if (!input.files || input.files.length === 0) return;
      let imported = 0;
      for (const file of input.files) {
        try {
          await importConversation(file);
          imported++;
        } catch (e) {
          showToast(`Failed: ${file.name} - ${e.message}`, 'error');
        }
      }
      if (imported > 0) {
        showToast(`Imported ${imported} conversation${imported > 1 ? 's' : ''}`, 'success');
        fetchConversationHistory();
      }
    };
    input.click();
  }

  function toggleCollapse() {
    appState.sidebarCollapsed = !appState.sidebarCollapsed;
    localStorage.setItem('lcc_sidebar_collapsed', appState.sidebarCollapsed ? '1' : '');
  }
</script>

<aside class="sidebar" class:collapsed={appState.sidebarCollapsed}>
  <div class="sidebar-header">
    <div class="logo-mark">
      <svg width="10" height="12" viewBox="0 0 10 12" fill="none"><path d="M6 0L0 7h4l-1 5 6-7H5l1-5z" fill="#fff"/></svg>
    </div>
    <span class="logo-name">Light CC</span>
    <button class="sidebar-close-btn" onclick={toggleCollapse} title="Hide sidebar">&#9664;</button>
  </div>

  <button class="new-chat-btn" onclick={handleNewChat}>
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
      <path d="M6 1v10M1 6h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
    New conversation
  </button>

  <div class="sidebar-actions">
    <button class="sidebar-action-btn" onclick={exportConversation} title="Export conversation as Markdown">
      <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
        <path d="M6 1v7M3 5l3 3 3-3M2 10h8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      Export
    </button>
    <button class="sidebar-action-btn" onclick={handleImport} title="Import conversations from Markdown files">
      <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
        <path d="M6 8V1M3 4l3-3 3 3M2 10h8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      Import
    </button>
  </div>

  <div class="sidebar-section-label">Conversations</div>

  <div class="sidebar-search">
    <input type="text" placeholder="Search conversations..." value={searchQuery} oninput={onSearch}>
  </div>

  <div class="chat-list">
    {#each filteredLocal as conv (conv.id)}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="chat-item"
        class:active={conv.id === appState.currentId}
        onclick={() => handleSwitchChat(conv.id)}
      >
        <div class="chat-item-dot"></div>
        <span class="chat-item-title">{conv.title}</span>
        <button class="chat-item-delete" onclick={(e) => { e.stopPropagation(); handleDeleteLocal(conv.id); }} title="Delete">&times;</button>
      </div>
    {/each}

    {#if filteredServer.length > 0}
      <div class="sidebar-section-label" style="margin-top:12px">History</div>
      {#each filteredServer as conv (conv.id)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="chat-item"
          onclick={() => handleResumeServer(conv.id)}
        >
          <div class="chat-item-dot" style="opacity:0.4"></div>
          <span class="chat-item-title">{conv.title}</span>
          <button class="chat-item-delete" onclick={(e) => { e.stopPropagation(); handleDeleteServer(conv.id); }} title="Delete">&times;</button>
        </div>
      {/each}
    {/if}
  </div>

  <div class="sidebar-footer">
    <div class="theme-selector" role="radiogroup" aria-label="Color theme">
      <span class="theme-label">Theme</span>
      {#each THEMES as t (t.name)}
        <button
          class="theme-dot"
          class:active={appState.theme === t.name}
          style:background={t.color}
          title={t.label}
          role="radio"
          aria-label="{t.label} theme"
          aria-checked={appState.theme === t.name}
          onclick={() => setTheme(t.name)}
        ></button>
      {/each}
    </div>
    <StatusBar />
    <button class="logout-btn" onclick={logout}>Sign Out</button>
  </div>
</aside>

{#if appState.sidebarCollapsed}
  <button class="sidebar-open-btn" onclick={toggleCollapse} aria-label="Show sidebar">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
      <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
  </button>
{/if}

<style>
  .sidebar {
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    transition: margin-left 0.2s ease, opacity 0.2s ease;
    width: var(--sidebar-w);
    flex-shrink: 0;
  }
  .sidebar.collapsed {
    margin-left: calc(-1 * var(--sidebar-w));
    opacity: 0;
    pointer-events: none;
  }

  .sidebar-header {
    padding: 18px 16px 14px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .logo-mark {
    width: 18px; height: 18px;
    background: linear-gradient(135deg, var(--accent) 0%, #a78bfa 100%);
    border-radius: 3px;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 0 10px rgba(99,102,241,0.35);
    flex-shrink: 0;
  }

  .logo-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: 0.04em;
  }

  .sidebar-close-btn {
    background: none;
    border: none;
    color: var(--muted);
    cursor: pointer;
    padding: 2px 4px;
    font-size: 12px;
    line-height: 1;
    transition: color 0.12s;
    margin-left: auto;
  }
  .sidebar-close-btn:hover { color: var(--fg); }

  .new-chat-btn {
    margin: 12px 12px 8px;
    padding: 8px 12px;
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    color: var(--fg-dim);
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    cursor: pointer;
    display: flex; align-items: center; gap: 8px;
    transition: all 0.15s;
    letter-spacing: 0.03em;
  }
  .new-chat-btn:hover {
    border-color: var(--accent);
    color: var(--accent-soft);
    background: var(--accent-glow);
  }

  .sidebar-actions {
    display: flex;
    gap: 6px;
    padding: 0 12px;
  }
  .sidebar-action-btn {
    flex: 1;
    padding: 5px 8px;
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    color: var(--muted);
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    cursor: pointer;
    display: flex; align-items: center; gap: 5px; justify-content: center;
    transition: all 0.15s;
    letter-spacing: 0.03em;
  }
  .sidebar-action-btn:hover {
    border-color: var(--accent);
    color: var(--accent-soft);
  }

  .sidebar-section-label {
    padding: 12px 16px 4px;
    font-size: 11px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 500;
  }

  .sidebar-search {
    padding: 4px 12px 8px;
  }
  .sidebar-search input {
    width: 100%;
    padding: 6px 10px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 4px;
    color: var(--fg);
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    outline: none;
  }
  .sidebar-search input:focus { border-color: var(--accent); }
  .sidebar-search input::placeholder { color: var(--muted); }

  .chat-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 8px;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  .chat-item {
    padding: 7px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    color: var(--dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transition: all 0.12s;
    border: 1px solid transparent;
    margin-bottom: 1px;
    display: flex; align-items: center; gap: 7px;
    background: none;
    width: 100%;
    text-align: left;
    font-family: 'Geist Mono', monospace;
  }
  .chat-item:hover { color: var(--fg); background: var(--border); }
  .chat-item.active {
    color: var(--fg-bright);
    background: var(--surface2);
    border-color: var(--border2);
  }

  .chat-item-dot {
    width: 4px; height: 4px;
    border-radius: 50%;
    background: var(--muted);
    flex-shrink: 0;
  }
  .chat-item.active .chat-item-dot { background: var(--accent-soft); }

  .chat-item-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .chat-item-delete {
    opacity: 0;
    flex-shrink: 0;
    background: none;
    border: none;
    color: var(--muted);
    cursor: pointer;
    font-size: 11px;
    padding: 0 2px;
    transition: opacity 0.15s, color 0.15s;
  }
  .chat-item:hover .chat-item-delete { opacity: 1; }
  .chat-item-delete:hover { color: var(--red); }

  .sidebar-footer {
    padding: 12px 16px;
    border-top: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 8px;
  }

  .theme-selector {
    display: flex;
    gap: 6px;
    align-items: center;
    padding: 4px 0;
  }
  .theme-label {
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-right: 4px;
  }
  .theme-dot {
    width: 16px; height: 16px;
    border-radius: 50%;
    border: 2px solid transparent;
    cursor: pointer;
    transition: border-color 0.15s, transform 0.15s;
    flex-shrink: 0;
    padding: 0;
  }
  .theme-dot:hover { transform: scale(1.15); }
  .theme-dot.active { border-color: var(--fg-bright); }

  .logout-btn {
    padding: 6px 10px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    color: var(--fg-dim);
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s;
    text-align: center;
  }
  .logout-btn:hover { border-color: var(--accent); color: var(--accent-soft); }

  .sidebar-open-btn {
    position: fixed;
    top: 14px;
    left: 8px;
    z-index: 201;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 4px;
    color: var(--fg-dim);
    width: 28px;
    height: 28px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s;
  }
  .sidebar-open-btn:hover { border-color: var(--accent); color: var(--fg); }

  @media (max-width: 768px) {
    .sidebar {
      position: fixed;
      left: 0; top: 0; bottom: 0;
      width: 280px;
      z-index: 200;
      transform: translateX(-100%);
      transition: transform 0.2s ease;
      margin-left: 0;
      opacity: 1;
      pointer-events: auto;
    }
    .sidebar:not(.collapsed) {
      transform: translateX(0);
    }
    .sidebar.collapsed {
      transform: translateX(-100%);
      margin-left: 0;
      opacity: 1;
    }
  }
</style>
