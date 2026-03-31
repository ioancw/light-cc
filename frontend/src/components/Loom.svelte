<script>
  import { onMount, onDestroy } from 'svelte';
  import { appState, newConversation, currentConversation } from '../state.svelte.js';
  import { connect, disconnect, send } from '../ws.js';
  import { modelLabel } from '../lib/utils.js';
  import Sidebar from './Sidebar.svelte';
  import ChatArea from './ChatArea.svelte';
  import InputBar from './InputBar.svelte';
  import PermissionDialog from './PermissionDialog.svelte';
  import Toast from './Toast.svelte';
  import FilePanel from './FilePanel.svelte';

  let filePanelRef = $state(null);

  onMount(() => {
    newConversation();
    connect();
    document.addEventListener('keydown', handleGlobalKeydown);
  });

  onDestroy(() => {
    disconnect();
    document.removeEventListener('keydown', handleGlobalKeydown);
  });

  function handleGlobalKeydown(e) {
    const tag = document.activeElement?.tagName;
    const inInput = tag === 'TEXTAREA' || tag === 'INPUT';

    // Ctrl+B -- toggle sidebar
    if (e.ctrlKey && e.key === 'b') {
      e.preventDefault();
      appState.sidebarCollapsed = !appState.sidebarCollapsed;
      localStorage.setItem('lcc_sidebar_collapsed', appState.sidebarCollapsed ? '1' : '');
      return;
    }

    // Ctrl+K -- new chat
    if (e.ctrlKey && e.key === 'k') {
      e.preventDefault();
      newConversation();
      send('clear_conversation', {});
      return;
    }

    // Escape -- close file panel or permission dialog (permission handled by its own component)
    if (e.key === 'Escape') {
      if (filePanelRef) {
        // FilePanel handles its own close via overlay click, but this catches keyboard
      }
      return;
    }

    // Focus input on printable key when not already in a text field
    if (!inInput && !e.ctrlKey && !e.altKey && !e.metaKey && e.key.length === 1) {
      const textarea = document.querySelector('.input-textarea');
      if (textarea) textarea.focus();
    }
  }

  function onModelChange(e) {
    const model = e.target.value;
    if (model) {
      send('set_model', { model });
      localStorage.setItem('lcc_model', model);
      appState.currentModel = model;
    }
  }

  let topbarTitle = $derived(currentConversation()?.title || 'New conversation');
</script>

<div class="app" class:sidebar-hidden={appState.sidebarCollapsed}>
  <Sidebar />

  <main class="main">
    <div class="topbar">
      <span class="topbar-title">{topbarTitle}</span>
      <div class="topbar-actions">
        <button class="topbar-btn" onclick={() => filePanelRef?.toggle()} title="File browser">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <path d="M2 3h4l2 2h6v8H2V3z" stroke="currentColor" stroke-width="1.3" fill="none"/>
          </svg>
        </button>
        <select class="model-selector" value={appState.currentModel} onchange={onModelChange}>
          {#each appState.availableModels as model (model)}
            <option value={model}>{modelLabel(model)}</option>
          {/each}
        </select>
      </div>
    </div>

    <ChatArea />
    <InputBar />
  </main>
</div>

<FilePanel bind:this={filePanelRef} />
<PermissionDialog />
<Toast />

<style>
  .app {
    display: grid;
    grid-template-columns: var(--sidebar-w) 1fr;
    height: 100vh;
  }
  .app.sidebar-hidden {
    grid-template-columns: 0 1fr;
  }
  .app.sidebar-hidden .topbar {
    padding-left: 48px;
  }

  .main {
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
    background: var(--bg);
    position: relative;
  }

  .topbar {
    padding: 0 24px;
    height: 52px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    flex-shrink: 0;
  }

  .topbar-title {
    font-size: 12px;
    color: var(--fg-bright);
    font-weight: 500;
    letter-spacing: 0.04em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .topbar-actions {
    display: flex; align-items: center; gap: 8px;
  }

  .topbar-btn {
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 4px;
    color: var(--fg-dim);
    width: 28px; height: 28px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: all 0.15s;
    padding: 0;
  }
  .topbar-btn:hover { border-color: var(--accent); color: var(--accent-soft); }

  .model-selector {
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 4px;
    color: var(--fg-dim);
    font-size: 11px;
    padding: 4px 6px;
    outline: none;
    cursor: pointer;
    font-family: 'Geist Mono', monospace;
  }
  .model-selector:focus { border-color: var(--accent); }
  .model-selector option { background: var(--surface); color: var(--fg); }

  @media (max-width: 768px) {
    .app {
      grid-template-columns: 1fr;
    }
    .topbar {
      padding: 0 12px;
      height: 44px;
    }
    .topbar-title {
      max-width: 120px;
    }
    .topbar-actions { gap: 4px; }
    .model-selector { max-width: 90px; font-size: 10px; }
  }
</style>
