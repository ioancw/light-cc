<script>
  import { appState, sortedConversations, newConversation, switchConversation, viewport } from '../state.svelte.js';
  import { fetchConversationHistory, deleteServerConversation, importConversation, renameConversation, searchConversations } from '../api.js';
  import { send } from '../ws.js';
  import { debounce } from '../lib/utils.js';
  import { showToast } from '../state.svelte.js';

  function closeSidebarOnMobile() {
    if (viewport.isMobile) {
      appState.sidebarCollapsed = true;
      localStorage.setItem('lcc_sidebar_collapsed', '1');
    }
  }

  let searchQuery = $state('');
  let searchResults = $state([]);
  let isSearching = $state(false);

  const debouncedSearch = debounce(async (value) => {
    if (value.length >= 2) {
      isSearching = true;
      fetchConversationHistory(value);
      searchResults = await searchConversations(value);
      isSearching = false;
    } else if (value.length === 0) {
      searchResults = [];
      isSearching = false;
      fetchConversationHistory();
    }
  }, 300);

  function onSearch(e) {
    searchQuery = e.target.value;
    debouncedSearch(searchQuery);
  }

  function isScheduled(conv) {
    return conv.title?.startsWith('[Scheduled]');
  }

  function displayTitle(conv) {
    return isScheduled(conv) ? conv.title.replace(/^\[Scheduled\]\s*/, '') : conv.title;
  }

  let filteredConversations = $derived(
    sortedConversations()
      .filter(c => !isScheduled(c))
      .filter(c => !searchQuery || c.title.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  // Time-grouped conversations
  function getTimeGroup(ts) {
    if (!ts) return 'Older';
    const now = new Date();
    const d = new Date(ts);
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startOfYesterday = new Date(startOfToday - 86400000);
    const startOf7Days = new Date(startOfToday - 6 * 86400000);
    if (d >= startOfToday) return 'Today';
    if (d >= startOfYesterday) return 'Yesterday';
    if (d >= startOf7Days) return 'Last 7 days';
    return 'Older';
  }

  let groupedConversations = $derived.by(() => {
    const groups = [];
    const order = ['Today', 'Yesterday', 'Last 7 days', 'Older'];
    const grouped = {};
    for (const conv of filteredConversations) {
      const group = getTimeGroup(conv.updatedAt || conv.createdAt);
      if (!grouped[group]) grouped[group] = [];
      grouped[group].push(conv);
    }
    for (const label of order) {
      if (grouped[label]?.length) {
        groups.push({ label, convs: grouped[label] });
      }
    }
    return groups;
  });

  let filteredScheduled = $derived(
    sortedConversations()
      .filter(c => isScheduled(c))
      .filter(c => !searchQuery || c.title.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  function handleNewChat() {
    newConversation();
    closeSidebarOnMobile();
  }

  function handleSwitchChat(id) {
    let conv = appState.conversations[id];
    // If clicking a search result that isn't in the map yet, create a stub
    if (!conv && id.startsWith('srv_')) {
      const serverId = id.slice(4);
      appState.conversations[id] = {
        id,
        serverId,
        title: 'Loading...',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
        titleGenerated: true,
        pinned: false,
        totalTokens: 0,
        stub: true,
      };
      conv = appState.conversations[id];
    }
    switchConversation(id);
    if (conv && conv.serverId && conv.messages.length === 0) {
      send('resume_conversation', { conversation_id: conv.serverId }, conv.serverId);
    }
    closeSidebarOnMobile();
  }

  function handleDelete(id) {
    const conv = appState.conversations[id];
    // Delete from server if it has been persisted
    if (conv?.serverId) {
      deleteServerConversation(conv.serverId);
    }
    // Remove locally
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

  // Inline rename
  let renamingId = $state(null);
  let renameValue = $state('');

  function startRename(conv) {
    renamingId = conv.id;
    renameValue = conv.title;
  }

  function commitRename(conv) {
    const trimmed = renameValue.trim();
    if (trimmed && trimmed !== conv.title) {
      conv.title = trimmed;
      conv.titleGenerated = true;
      // Persist to server
      if (conv.serverId) {
        renameConversation(conv.serverId, trimmed);
      }
    }
    renamingId = null;
  }

  function cancelRename() {
    renamingId = null;
  }

  // Delete confirmation
  let confirmingDeleteId = $state(null);

  function requestDelete(id) {
    confirmingDeleteId = id;
  }

  function confirmDelete(id) {
    confirmingDeleteId = null;
    handleDelete(id);
  }

  function cancelDelete() {
    confirmingDeleteId = null;
  }

  function renameKeydown(e, conv) {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitRename(conv);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelRename();
    }
  }

  // Swipe-left to close sidebar on mobile.
  // Only the first touchpoint is tracked — drop multi-touch to avoid fighting
  // the scrollable chat list pinch-zoom.
  let swipeStartX = 0;
  let swipeStartY = 0;
  let swipeTracking = false;

  function onTouchStart(e) {
    if (!viewport.isMobile || appState.sidebarCollapsed) return;
    if (e.touches.length !== 1) return;
    swipeStartX = e.touches[0].clientX;
    swipeStartY = e.touches[0].clientY;
    swipeTracking = true;
  }

  function onTouchMove(e) {
    if (!swipeTracking) return;
    const dx = e.touches[0].clientX - swipeStartX;
    const dy = Math.abs(e.touches[0].clientY - swipeStartY);
    // Horizontal left-swipe beats vertical scrolling if we've moved enough.
    if (dx < -60 && dy < 40) {
      swipeTracking = false;
      appState.sidebarCollapsed = true;
      localStorage.setItem('lcc_sidebar_collapsed', '1');
    }
  }

  function onTouchEnd() {
    swipeTracking = false;
  }
</script>

<aside
  class="sidebar"
  class:collapsed={appState.sidebarCollapsed}
  ontouchstart={onTouchStart}
  ontouchmove={onTouchMove}
  ontouchend={onTouchEnd}
>
  <div class="sidebar-header">
    <span class="logo-chip" aria-hidden="true">/</span>
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
    {#each groupedConversations as group (group.label)}
      <div class="sidebar-section-label time-group">{group.label}</div>
      {#each group.convs as conv (conv.id)}
        <div
          class="chat-item"
          class:active={conv.id === appState.currentId}
          onclick={() => handleSwitchChat(conv.id)}
          ondblclick={(e) => { e.stopPropagation(); startRename(conv); }}
          onkeydown={(e) => { if (e.key === 'Enter') handleSwitchChat(conv.id); }}
          role="button"
          tabindex="0"
        >
          {#if conv.messages?.some(m => m.streaming)}
            <div class="chat-item-dot streaming"></div>
          {:else if appState.loadingConversations.has(conv.serverId) || appState.loadingConversations.has(conv.id)}
            <div class="chat-item-dot loading"></div>
          {:else}
            <div class="chat-item-dot" class:stub={conv.stub}></div>
          {/if}
          {#if renamingId === conv.id}
            <!-- svelte-ignore a11y_autofocus -->
            <input
              class="chat-item-rename"
              type="text"
              bind:value={renameValue}
              onkeydown={(e) => renameKeydown(e, conv)}
              onblur={() => commitRename(conv)}
              onclick={(e) => e.stopPropagation()}
              autofocus
            />
          {:else if confirmingDeleteId === conv.id}
            <span class="chat-item-confirm">delete?</span>
            <button class="chat-item-confirm-btn yes" onclick={(e) => { e.stopPropagation(); confirmDelete(conv.id); }}>yes</button>
            <button class="chat-item-confirm-btn no" onclick={(e) => { e.stopPropagation(); cancelDelete(); }}>no</button>
          {:else}
            <span class="chat-item-title">{conv.title}</span>
          {/if}
          {#if confirmingDeleteId !== conv.id}
            <button class="chat-item-delete" onclick={(e) => { e.stopPropagation(); requestDelete(conv.id); }} title="Delete">&times;</button>
          {/if}
        </div>
      {/each}
    {/each}

    {#if filteredScheduled.length > 0}
      <div class="sidebar-section-label" style="margin-top:12px">
        <svg class="schedule-icon" width="10" height="10" viewBox="0 0 16 16" fill="none">
          <path d="M5 1v3M11 1v3M3 6h10M2 3h12a1 1 0 011 1v10a1 1 0 01-1 1H2a1 1 0 01-1-1V4a1 1 0 011-1z" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M8 8v2.5l1.5 1" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Scheduled
      </div>
      {#each filteredScheduled as conv (conv.id)}
        <div
          class="chat-item scheduled"
          class:active={conv.id === appState.currentId}
          onclick={() => handleSwitchChat(conv.id)}
          onkeydown={(e) => { if (e.key === 'Enter') handleSwitchChat(conv.id); }}
          role="button"
          tabindex="0"
        >
          <svg class="chat-item-schedule-icon" width="11" height="11" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.3"/>
            <path d="M8 5v3.5l2.5 1.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="chat-item-title">{displayTitle(conv)}</span>
          {#if confirmingDeleteId === conv.id}
            <span class="chat-item-confirm">delete?</span>
            <button class="chat-item-confirm-btn yes" onclick={(e) => { e.stopPropagation(); confirmDelete(conv.id); }}>yes</button>
            <button class="chat-item-confirm-btn no" onclick={(e) => { e.stopPropagation(); cancelDelete(); }}>no</button>
          {:else}
            <button class="chat-item-delete" onclick={(e) => { e.stopPropagation(); requestDelete(conv.id); }} title="Delete">&times;</button>
          {/if}
        </div>
      {/each}
    {/if}

    {#if searchQuery.length >= 2 && searchResults.length > 0}
      {@const visibleServerIds = new Set(
        Object.values(appState.conversations).filter(c => c.serverId).map(c => c.serverId)
      )}
      {@const filteredSearchResults = searchResults.filter(r => !visibleServerIds.has(r.conversation_id))}
      {#if filteredSearchResults.length > 0}
        <div class="sidebar-section-label" style="margin-top:12px">
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" style="vertical-align:-1px;margin-right:2px">
            <circle cx="7" cy="7" r="5.5" stroke="currentColor" stroke-width="1.3" fill="none"/>
            <path d="M11 11l3.5 3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
          </svg>
          Content matches
        </div>
        {#each filteredSearchResults as result (result.conversation_id)}
          <div
            class="chat-item search-result"
            onclick={() => handleSwitchChat('srv_' + result.conversation_id)}
            onkeydown={(e) => { if (e.key === 'Enter') handleSwitchChat('srv_' + result.conversation_id); }}
            role="button"
            tabindex="0"
          >
            <div class="chat-item-dot stub"></div>
            <div class="search-result-content">
              <span class="chat-item-title">{result.title}</span>
              <span class="search-result-snippet">{result.snippet}</span>
            </div>
          </div>
        {/each}
      {/if}
    {/if}

    {#if searchQuery.length >= 2 && isSearching}
      <div class="search-status">Searching...</div>
    {/if}

    {#if searchQuery.length >= 2 && !isSearching && searchResults.length === 0 && filteredConversations.length === 0}
      <div class="search-status">No results found</div>
    {/if}
  </div>

  <div class="sidebar-footer">
    <div class="shortcut-hints">
      <span><span class="kbd">Ctrl+B</span> sidebar</span>
      <span><span class="kbd">Ctrl+K</span> new chat</span>
    </div>
  </div>
</aside>

{#if appState.sidebarCollapsed}
  <button class="sidebar-open-btn" onclick={toggleCollapse} aria-label="Show sidebar">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
      <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
  </button>
{/if}

{#if viewport.isMobile && !appState.sidebarCollapsed}
  <button
    class="sidebar-backdrop"
    aria-label="Close sidebar"
    onclick={toggleCollapse}
  ></button>
{/if}

<style>
  .sidebar {
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    transition: margin-left 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease;
    width: var(--sidebar-w);
    flex-shrink: 0;
  }
  .sidebar.collapsed {
    width: 0;
    min-width: 0;
    opacity: 0;
    pointer-events: none;
    overflow: hidden;
  }

  .sidebar-header {
    padding: 18px 16px 14px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .logo-chip {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    background: var(--accent);
    color: var(--bg);
    border-radius: 6px;
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: 15px;
    letter-spacing: -0.04em;
    line-height: 1;
    flex-shrink: 0;
    /* Subtle bottom shadow gives the chip a hint of physicality —
       reads as a pressable "command key" rather than flat decoration. */
    box-shadow: 0 1px 0 color-mix(in srgb, var(--accent) 55%, #000);
    transition: transform 0.15s ease;
  }
  .sidebar-header:hover .logo-chip { transform: translateY(-1px); }

  .logo-name {
    font-family: var(--font-ui);
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: -0.01em;
    line-height: 1;
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
    padding: 9px 12px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    color: var(--fg-dim);
    font-family: var(--font-ui);
    font-size: 13px;
    cursor: pointer;
    display: flex; align-items: center; gap: 8px;
    transition: all 0.15s ease;
  }
  .new-chat-btn:hover {
    background: var(--border);
    color: var(--fg-bright);
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
    border: none;
    border-radius: var(--radius);
    color: var(--muted);
    font-family: var(--font-ui);
    font-size: 12px;
    cursor: pointer;
    display: flex; align-items: center; gap: 5px; justify-content: center;
    transition: color 0.15s;
  }
  .sidebar-action-btn:hover {
    color: var(--fg-dim);
  }

  .sidebar-section-label {
    padding: 14px 12px 4px;
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 600;
  }
  .sidebar-section-label.time-group {
    padding: 10px 12px 2px;
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--muted);
    font-weight: 500;
    text-transform: none;
  }

  .sidebar-search {
    padding: 4px 12px 8px;
  }
  .sidebar-search input {
    width: 100%;
    padding: 7px 10px;
    background: var(--bg);
    border: 1px solid var(--border2);
    border-radius: 6px;
    color: var(--fg);
    font-family: var(--font-ui);
    font-size: 13px;
    outline: none;
  }
  .sidebar-search input:focus { border-color: var(--muted); }
  .sidebar-search input::placeholder { color: var(--muted); }

  .chat-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 8px;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  .chat-item {
    position: relative;
    padding: 8px 10px 8px 14px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    color: var(--fg-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transition: background 0.15s ease, color 0.15s ease;
    border: none;
    margin-bottom: 1px;
    display: flex; align-items: center; gap: 8px;
    background: none;
    width: 100%;
    text-align: left;
    font-family: var(--font-ui);
  }
  .chat-item::before {
    content: '';
    position: absolute;
    left: 4px;
    top: 8px;
    bottom: 8px;
    width: 2px;
    border-radius: 1px;
    background: transparent;
    transition: background 0.15s ease;
  }
  .chat-item:hover { color: var(--fg); background: var(--surface2); }
  .chat-item:hover::before { background: var(--border2); }
  .chat-item.active {
    color: var(--fg-bright);
    background: var(--surface2);
    font-weight: 500;
  }
  .chat-item.active::before { background: var(--accent); }

  .chat-item-dot {
    width: 5px; height: 5px;
    border-radius: 50%;
    background: var(--muted);
    flex-shrink: 0;
    transition: background 0.2s, transform 0.2s;
  }
  .chat-item-dot.stub { opacity: 0.4; }
  .chat-item.active .chat-item-dot { background: var(--accent-soft); transform: scale(1.2); }
  .chat-item-dot.streaming {
    background: var(--accent-soft);
    animation: streaming-pulse 1.2s ease-in-out infinite;
  }
  @keyframes streaming-pulse {
    0%, 100% { opacity: 0.3; transform: scale(1); }
    50% { opacity: 1; transform: scale(1.3); }
  }
  .chat-item-dot.loading {
    background: var(--fg-dim);
    animation: loading-shimmer 1.6s ease-in-out infinite;
  }
  @keyframes loading-shimmer {
    0%, 100% { opacity: 0.25; }
    50% { opacity: 0.7; }
  }

  .chat-item-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .chat-item-confirm {
    font-size: 11px;
    color: var(--red);
    letter-spacing: 0.05em;
    flex-shrink: 0;
  }
  .chat-item-confirm-btn {
    background: none;
    border: 1px solid var(--border2);
    border-radius: 3px;
    font-family: var(--font-ui);
    font-size: 10px;
    letter-spacing: 0.05em;
    padding: 1px 8px;
    cursor: pointer;
    transition: all 0.12s ease;
  }
  .chat-item-confirm-btn.yes {
    color: var(--red);
    border-color: color-mix(in srgb, var(--red) 40%, transparent);
  }
  .chat-item-confirm-btn.yes:hover {
    background: var(--red-soft);
    border-color: var(--red);
  }
  .chat-item-confirm-btn.no {
    color: var(--muted);
  }
  .chat-item-confirm-btn.no:hover {
    color: var(--fg-dim);
    border-color: var(--muted);
  }

  .chat-item-rename {
    flex: 1;
    background: var(--surface2);
    border: 1px solid var(--accent);
    border-radius: 3px;
    color: var(--fg-bright);
    font-family: var(--font-ui);
    font-size: 11px;
    padding: 2px 6px;
    outline: none;
    min-width: 0;
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

  .chat-item.scheduled { color: var(--muted); }
  .chat-item.scheduled:hover { color: var(--fg); }
  .chat-item.scheduled.active { color: var(--fg-bright); }

  .chat-item-schedule-icon {
    flex-shrink: 0;
    color: var(--accent-soft);
    opacity: 0.7;
  }
  .chat-item.scheduled.active .chat-item-schedule-icon { opacity: 1; }

  .schedule-icon {
    vertical-align: -1px;
    margin-right: 2px;
  }

  .sidebar-footer {
    padding: 10px 16px;
    border-top: 1px solid var(--border);
  }

  .shortcut-hints {
    display: flex;
    gap: 12px;
    justify-content: center;
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.04em;
  }

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
  .search-result-content {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .search-result-snippet {
    font-size: 10px;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.3;
  }
  .chat-item.search-result {
    align-items: flex-start;
    padding: 6px 10px;
  }
  .chat-item.search-result .chat-item-dot {
    margin-top: 4px;
  }
  .search-status {
    padding: 8px 16px;
    font-size: 11px;
    color: var(--muted);
    text-align: center;
    letter-spacing: 0.03em;
  }

  .sidebar-open-btn:hover { border-color: var(--accent); color: var(--fg); }

  .sidebar-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.65);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
    border: none;
    padding: 0;
    margin: 0;
    z-index: 199;
    cursor: pointer;
    animation: backdrop-fade-in 0.15s ease;
  }
  @keyframes backdrop-fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  @media (max-width: 768px) {
    .sidebar {
      position: fixed;
      left: 0; top: 0; bottom: 0;
      width: min(280px, 85vw);
      z-index: 200;
      transform: translateX(-100%);
      transition: transform 0.2s ease;
      margin-left: 0;
      opacity: 1;
      pointer-events: auto;
      box-shadow: 4px 0 20px rgba(0, 0, 0, 0.3);
    }
    .sidebar:not(.collapsed) {
      transform: translateX(0);
    }
    .sidebar.collapsed {
      transform: translateX(-100%);
      margin-left: 0;
      opacity: 1;
    }
    .sidebar-open-btn {
      width: 44px;
      height: 44px;
      top: 4px;
      left: 6px;
    }
    .chat-item {
      padding: 9px 8px 9px 14px;
      min-height: 44px;
      font-size: 14px;
      margin-bottom: 0;
    }
    .chat-item::before { top: 10px; bottom: 10px; }
    .chat-item-delete {
      opacity: 1;
      min-width: 44px;
      min-height: 44px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
    }
    .shortcut-hints { display: none; }
    .sidebar-footer {
      display: none;
    }
    .new-chat-btn {
      padding: 12px;
      min-height: 44px;
      font-size: 14px;
    }
    .sidebar-action-btn {
      padding: 10px 8px;
      min-height: 40px;
      font-size: 13px;
    }
    .sidebar-search input {
      padding: 10px 12px;
      font-size: 14px;
      min-height: 40px;
    }
  }
</style>
