<script>
  import { onMount, onDestroy } from 'svelte';
  import { appState, newConversation, currentConversation, viewport } from '../state.svelte.js';
  import { connect, disconnect, send } from '../ws.js';
  import { modelLabel, modelShortLabel } from '../lib/utils.js';
  import Sidebar from './Sidebar.svelte';
  import ChatArea from './ChatArea.svelte';
  import InputBar from './InputBar.svelte';
  import PermissionDialog from './PermissionDialog.svelte';
  import Toast from './Toast.svelte';
  import FilePanel from './FilePanel.svelte';
  import StatusBar from './StatusBar.svelte';
  import Settings from './Settings.svelte';
  import AgentPanel from './AgentPanel.svelte';
  import MemoryPanel from './MemoryPanel.svelte';

  let filePanelRef = $state(null);
  let agentPanelRef = $state(null);
  let memoryPanelRef = $state(null);
  let settingsOpen = $state(false);

  // Custom model dropdown
  let modelDropdownOpen = $state(false);
  let modelDropdownEl = $state(null);

  // Mobile overflow menu (consolidates the four panel-toggle buttons)
  let overflowMenuOpen = $state(false);
  let overflowMenuEl = $state(null);

  function openPanel(fn) {
    overflowMenuOpen = false;
    fn?.();
  }

  function onClickOutsideOverflow(e) {
    if (overflowMenuEl && !overflowMenuEl.contains(e.target)) {
      overflowMenuOpen = false;
    }
  }

  $effect(() => {
    if (overflowMenuOpen) {
      document.addEventListener('click', onClickOutsideOverflow, true);
      return () => document.removeEventListener('click', onClickOutsideOverflow, true);
    }
  });

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
        <div class="desktop-actions">
          <button class="topbar-btn" onclick={() => settingsOpen = true} title="Settings">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.3" fill="none"/>
              <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
            </svg>
          </button>
          <button class="topbar-btn" onclick={() => agentPanelRef?.toggle()} title="Agents">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
              <rect x="3" y="3" width="10" height="10" rx="2" stroke="currentColor" stroke-width="1.3" fill="none"/>
              <circle cx="6" cy="7" r="0.8" fill="currentColor"/>
              <circle cx="10" cy="7" r="0.8" fill="currentColor"/>
              <path d="M6 10h4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
            </svg>
          </button>
          <button class="topbar-btn" onclick={() => memoryPanelRef?.toggle()} title="Memory">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
              <path d="M4 2h6l2 2v10H4V2z" stroke="currentColor" stroke-width="1.3" fill="none"/>
              <path d="M6 6h4M6 9h4M6 12h2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
            </svg>
          </button>
          <button class="topbar-btn" onclick={() => filePanelRef?.toggle()} title="File browser">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
              <path d="M2 3h4l2 2h6v8H2V3z" stroke="currentColor" stroke-width="1.3" fill="none"/>
            </svg>
          </button>
        </div>

        <div class="overflow-menu" bind:this={overflowMenuEl}>
          <button
            class="topbar-btn overflow-trigger"
            onclick={() => overflowMenuOpen = !overflowMenuOpen}
            aria-label="More actions"
            aria-expanded={overflowMenuOpen}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="3.5" r="1.5" fill="currentColor"/>
              <circle cx="8" cy="8" r="1.5" fill="currentColor"/>
              <circle cx="8" cy="12.5" r="1.5" fill="currentColor"/>
            </svg>
          </button>
          {#if overflowMenuOpen}
            <div class="overflow-menu-dropdown">
              <button class="overflow-item" onclick={() => openPanel(() => settingsOpen = true)}>Settings</button>
              <button class="overflow-item" onclick={() => openPanel(() => agentPanelRef?.toggle())}>Agents</button>
              <button class="overflow-item" onclick={() => openPanel(() => memoryPanelRef?.toggle())}>Memory</button>
              <button class="overflow-item" onclick={() => openPanel(() => filePanelRef?.toggle())}>Files</button>
            </div>
          {/if}
        </div>
        <div class="model-dropdown" bind:this={modelDropdownEl}>
          <button class="model-trigger" onclick={toggleModelDropdown} class:open={modelDropdownOpen} aria-label="Model: {modelLabel(appState.currentModel) || 'Model'}">
            <span class="model-label-long">{modelLabel(appState.currentModel) || 'Model'}</span>
            <span class="model-label-short">{modelShortLabel(appState.currentModel) || 'M'}</span>
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
<AgentPanel bind:this={agentPanelRef} />
<MemoryPanel bind:this={memoryPanelRef} />
<Settings bind:open={settingsOpen} />
<PermissionDialog />
<Toast />

<style>
  .app {
    display: grid;
    grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
    grid-template-rows: 100vh;
    grid-template-rows: 100dvh;
    grid-template-rows: var(--app-h, 100dvh);
    height: 100vh;
    height: 100dvh;
    height: var(--app-h, 100dvh);
    overflow: hidden;
  }
  .app.sidebar-hidden {
    grid-template-columns: 0 minmax(0, 1fr);
  }
  .app.sidebar-hidden .topbar {
    padding-left: 48px;
  }

  .main {
    display: flex;
    flex-direction: column;
    height: 100vh;
    height: 100dvh;
    height: var(--app-h, 100dvh);
    min-width: 0;
    overflow: hidden;
    background: var(--bg);
    position: relative;
  }

  .topbar {
    padding: 0 24px;
    height: 48px;
    border-bottom: 1px solid var(--border);
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 16px;
    flex-shrink: 0;
    background: var(--bg);
    position: relative;
    z-index: 10;
    min-width: 0;
  }

  .topbar-title {
    color: var(--fg-bright);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-ui);
    font-size: 14px;
    min-width: 0;
  }

  .topbar-status {
    min-width: 0;
  }

  .topbar-actions {
    display: flex; align-items: center; gap: 10px;
  }
  .desktop-actions {
    display: flex; align-items: center; gap: 10px;
  }
  .overflow-menu { display: none; position: relative; }
  .overflow-menu-dropdown {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    min-width: 160px;
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: 6px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    z-index: 200;
    overflow: hidden;
    animation: dropdown-in 0.15s ease;
  }
  .overflow-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 10px 14px;
    font-size: 14px;
    font-family: var(--font-ui);
    color: var(--fg-dim);
    cursor: pointer;
    background: none;
    border: none;
    border-bottom: 1px solid var(--border);
    transition: background 0.1s ease;
  }
  .overflow-item:last-child { border-bottom: none; }
  .overflow-item:hover { background: var(--surface2); color: var(--fg-bright); }

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
  .model-label-short { display: none; }
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

  @media (max-width: 900px) {
    .topbar-actions { gap: 4px; }
    .topbar-btn { width: 26px; height: 26px; }
  }

  @media (max-width: 768px) {
    .app, .app.sidebar-hidden {
      grid-template-columns: 1fr;
    }
    .topbar {
      padding: 0 12px;
      height: 48px;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
    }
    .topbar-status { display: none; }
    .topbar-title {
      min-width: 0;
    }
    .topbar-actions { gap: 4px; }
    .topbar-btn { width: 36px; height: 36px; }
    .desktop-actions { display: none; }
    .overflow-menu { display: block; }
    .model-trigger {
      font-size: 11px;
      padding: 6px 8px;
      min-height: 36px;
      letter-spacing: 0.02em;
      font-variant-numeric: tabular-nums;
    }
    .model-label-long { display: none; }
    .model-label-short { display: inline; font-weight: 600; }
    .model-menu { min-width: 160px; }
  }
</style>
