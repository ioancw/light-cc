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
  import StatusBar from './StatusBar.svelte';
  import Settings from './Settings.svelte';

  let filePanelRef = $state(null);
  let settingsOpen = $state(false);

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

  function selectModel(model) {
    if (model) {
      const conv = currentConversation();
      send('set_model', { model }, conv?.id);
      localStorage.setItem('lcc_model', model);
      appState.currentModel = model;
    }
    modelDropdownOpen = false;
  }

  // Custom model dropdown
  let modelDropdownOpen = $state(false);
  let modelDropdownEl = $state(null);

  function toggleModelDropdown() {
    modelDropdownOpen = !modelDropdownOpen;
  }

  function onClickOutsideModel(e) {
    if (modelDropdownEl && !modelDropdownEl.contains(e.target)) {
      modelDropdownOpen = false;
    }
  }

  $effect(() => {
    if (modelDropdownOpen) {
      document.addEventListener('click', onClickOutsideModel, true);
      return () => document.removeEventListener('click', onClickOutsideModel, true);
    }
  });

  let topbarTitle = $derived(currentConversation()?.title || 'New conversation');
</script>

<div class="app" class:sidebar-hidden={appState.sidebarCollapsed}>
  <Sidebar />

  <main class="main">
    <div class="topbar">
      <span class="topbar-title">{topbarTitle}</span>
      <div class="topbar-status">
        <StatusBar />
      </div>
      <div class="topbar-actions">
        <button class="topbar-btn" onclick={() => settingsOpen = true} title="Settings">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.3" fill="none"/>
            <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
          </svg>
        </button>
        <button class="topbar-btn" onclick={() => filePanelRef?.toggle()} title="File browser">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <path d="M2 3h4l2 2h6v8H2V3z" stroke="currentColor" stroke-width="1.3" fill="none"/>
          </svg>
        </button>
        <div class="model-dropdown" bind:this={modelDropdownEl}>
          <button class="model-trigger" onclick={toggleModelDropdown} class:open={modelDropdownOpen}>
            <span>{modelLabel(appState.currentModel) || 'Model'}</span>
            <svg class="model-chevron" width="8" height="8" viewBox="0 0 10 10" fill="none">
              <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
          {#if modelDropdownOpen}
            <div class="model-menu">
              {#each appState.availableModels as model (model)}
                <button
                  class="model-option"
                  class:selected={model === appState.currentModel}
                  onclick={() => selectModel(model)}
                >
                  <span class="model-option-label">{modelLabel(model)}</span>
                  {#if model === appState.currentModel}
                    <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
                      <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                  {/if}
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    </div>

    <ChatArea />
    <InputBar />
  </main>
</div>

<FilePanel bind:this={filePanelRef} />
<Settings bind:open={settingsOpen} />
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
    height: 48px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 16px;
    flex-shrink: 0;
    background: var(--bg);
    position: relative;
    z-index: 10;
  }

  .topbar-title {
    color: var(--fg-bright);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-ui);
    font-size: 14px;
  }

  .topbar-status {
    margin-left: auto;
    flex-shrink: 0;
  }

  .topbar-actions {
    display: flex; align-items: center; gap: 10px;
  }

  .topbar-btn {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: var(--muted);
    width: 30px; height: 30px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: color 0.15s, background 0.15s;
    padding: 0;
  }
  .topbar-btn:hover {
    color: var(--fg-dim);
    background: var(--surface2);
  }

  .model-dropdown {
    position: relative;
  }

  .model-trigger {
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 6px;
    color: var(--fg-dim);
    font-size: 12px;
    padding: 5px 10px;
    cursor: pointer;
    font-family: var(--font-ui);
    transition: border-color 0.15s, background 0.15s;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .model-trigger:hover {
    border-color: var(--muted);
  }
  .model-trigger.open {
    border-color: var(--muted);
    background: var(--surface2);
  }
  .model-chevron {
    transition: transform 0.2s ease;
  }
  .model-trigger.open .model-chevron {
    transform: rotate(180deg);
  }

  .model-menu {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    min-width: 180px;
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: 6px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    z-index: 200;
    overflow: hidden;
    animation: dropdown-in 0.15s ease;
  }
  @keyframes dropdown-in {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .model-option {
    padding: 8px 14px;
    font-size: 13px;
    font-family: var(--font-ui);
    color: var(--fg-dim);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    transition: background 0.1s ease;
    border: none;
    border-bottom: 1px solid var(--border);
    background: none;
    width: 100%;
    text-align: left;
  }
  .model-option:last-child { border-bottom: none; }
  .model-option:hover {
    background: var(--surface2);
    color: var(--fg-bright);
  }
  .model-option.selected {
    color: var(--accent-soft);
  }
  .model-option.selected svg {
    color: var(--accent-soft);
  }

  @media (max-width: 768px) {
    .app, .app.sidebar-hidden {
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
    .model-trigger { font-size: 10px; padding: 4px 8px; }
    .model-menu { min-width: 140px; }
  }
</style>
