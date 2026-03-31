<script>
  import { appState, showToast } from '../state.svelte.js';
  import { listFiles, readFile, uploadFile } from '../api.js';
  import { formatFileSize } from '../lib/utils.js';

  let open = $state(false);
  let currentPath = $state('');
  let entries = $state([]);
  let loading = $state(false);
  let error = $state(null);

  export function toggle() {
    open = !open;
    if (open) refresh();
  }

  async function refresh() {
    loading = true;
    error = null;
    try {
      entries = await listFiles(currentPath);
    } catch (e) {
      error = e.message;
      entries = [];
    }
    loading = false;
  }

  async function navigate(path) {
    currentPath = path;
    await refresh();
  }

  let breadcrumbs = $derived(() => {
    if (!currentPath) return [{ label: 'workspace', path: '' }];
    const parts = currentPath.split('/');
    const crumbs = [{ label: 'workspace', path: '' }];
    let cumulative = '';
    parts.forEach((part, i) => {
      cumulative += (i > 0 ? '/' : '') + part;
      crumbs.push({ label: part, path: cumulative });
    });
    return crumbs;
  });

  async function handleFileClick(entry) {
    if (entry.is_dir) {
      navigate(entry.path);
      return;
    }
    // Preview file and insert into input
    try {
      const data = await readFile(entry.path);
      const preview = data.content.length > 5000
        ? data.content.substring(0, 5000) + '\n\n... (truncated)'
        : data.content;
      // Dispatch event for InputBar to pick up
      window.dispatchEvent(new CustomEvent('lcc-insert-text', {
        detail: `[File: ${entry.name}]\n\`\`\`\n${preview}\n\`\`\``
      }));
      showToast(`${entry.name} (${formatFileSize(data.size)}) inserted`);
    } catch (e) {
      if (e.message.includes('400')) {
        downloadFile(entry.path, entry.name);
      } else {
        showToast(`Cannot read: ${e.message}`, 'error');
      }
    }
  }

  function downloadFile(path, name) {
    const a = document.createElement('a');
    a.href = `/api/files/download?path=${encodeURIComponent(path)}&token=${encodeURIComponent(appState.authToken)}`;
    a.download = name || path.split('/').pop() || 'file';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async function handleUpload() {
    const input = document.createElement('input');
    input.type = 'file';
    input.onchange = async () => {
      if (!input.files || !input.files[0]) return;
      try {
        const data = await uploadFile(currentPath, input.files[0]);
        showToast(`Uploaded ${data.path}`, 'success');
        refresh();
      } catch (e) {
        showToast(`Upload failed: ${e.message}`, 'error');
      }
    };
    input.click();
  }

  function close() {
    open = false;
  }
</script>

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="file-panel-overlay" onclick={close}></div>
  <div class="file-panel">
    <div class="file-panel-header">
      <span class="file-panel-title">Files</span>
      <button class="file-panel-upload" onclick={handleUpload}>Upload</button>
      <button class="file-panel-close" onclick={close}>&times;</button>
    </div>

    <div class="file-breadcrumb">
      {#each breadcrumbs() as crumb, i}
        {#if i > 0}<span class="breadcrumb-sep">/</span>{/if}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <span class="breadcrumb-item" onclick={() => navigate(crumb.path)}>{crumb.label}</span>
      {/each}
    </div>

    <div class="file-list">
      {#if loading}
        <div class="file-panel-empty">Loading...</div>
      {:else if error}
        <div class="file-panel-empty file-error">Error: {error}</div>
      {:else if entries.length === 0}
        <div class="file-panel-empty">Empty directory</div>
      {:else}
        {#if currentPath}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div class="file-entry" onclick={() => navigate(currentPath.split('/').slice(0, -1).join('/'))}>
            <span class="file-entry-icon">..</span>
            <span class="file-entry-name">..</span>
          </div>
        {/if}
        {#each entries as entry (entry.path || entry.name)}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div class="file-entry" onclick={() => handleFileClick(entry)}>
            <span class="file-entry-icon">{entry.is_dir ? '\u25B6' : '\u25AA'}</span>
            <span class="file-entry-name">{entry.name}</span>
            {#if entry.size != null}
              <span class="file-entry-size">{formatFileSize(entry.size)}</span>
            {/if}
          </div>
        {/each}
      {/if}
    </div>
  </div>
{/if}

<style>
  .file-panel-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.3);
    z-index: 299;
  }

  .file-panel {
    position: fixed;
    right: 0; top: 0; bottom: 0;
    width: 320px;
    max-width: 100%;
    background: var(--surface);
    border-left: 1px solid var(--border);
    z-index: 300;
    display: flex;
    flex-direction: column;
    animation: slide-in 0.2s ease;
    font-family: 'Geist Mono', monospace;
  }
  @keyframes slide-in {
    from { transform: translateX(100%); }
    to { transform: translateX(0); }
  }

  .file-panel-header {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 10px;
  }

  .file-panel-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: 0.05em;
    flex: 1;
  }

  .file-panel-upload {
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 3px;
    color: var(--fg-dim);
    padding: 3px 10px;
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .file-panel-upload:hover { border-color: var(--accent); color: var(--accent-soft); }

  .file-panel-close {
    background: none;
    border: none;
    color: var(--muted);
    font-size: 16px;
    cursor: pointer;
    padding: 0 4px;
    transition: color 0.12s;
  }
  .file-panel-close:hover { color: var(--fg); }

  .file-breadcrumb {
    padding: 8px 16px;
    font-size: 11px;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-wrap: wrap;
    gap: 2px;
    align-items: center;
  }
  .breadcrumb-sep { color: var(--border2); margin: 0 2px; }
  .breadcrumb-item {
    cursor: pointer;
    transition: color 0.12s;
  }
  .breadcrumb-item:hover { color: var(--accent-soft); }

  .file-list {
    flex: 1;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  .file-panel-empty {
    padding: 20px 16px;
    text-align: center;
    font-size: 11px;
    color: var(--muted);
  }
  .file-error { color: var(--red); }

  .file-entry {
    padding: 7px 16px;
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    font-size: 11px;
    color: var(--fg-dim);
    transition: background 0.12s;
    border-bottom: 1px solid var(--border);
  }
  .file-entry:hover { background: var(--surface2); }

  .file-entry-icon {
    color: var(--muted);
    font-size: 10px;
    width: 14px;
    text-align: center;
    flex-shrink: 0;
  }

  .file-entry-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .file-entry-size {
    color: var(--muted);
    font-size: 10px;
    flex-shrink: 0;
  }
</style>
