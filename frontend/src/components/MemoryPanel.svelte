<script>
  import { showToast } from '../state.svelte.js';
  import {
    listMemories, getMemory, createMemory, updateMemory, deleteMemory,
    getUserSettings, updateUserSettings,
  } from '../api.js';

  const MEMORY_TYPES = [
    'note', 'fact', 'preference', 'project', 'reference', 'feedback', 'user',
  ];

  let open = $state(false);
  let view = $state('list');  // 'list' | 'edit' | 'settings'
  let memories = $state([]);
  let loading = $state(false);
  let error = $state(null);

  // Filters
  let filterType = $state('');     // empty = all
  let filterSource = $state('');   // empty = all

  // Edit form state
  let editing = $state(null);      // current memory or null
  let form = $state(blankForm());
  let tagsText = $state('');       // comma-separated

  // Settings
  let settings = $state(null);

  export function toggle() {
    open = !open;
    if (open) {
      view = 'list';
      refresh();
    }
  }

  function close() { open = false; }

  function blankForm() {
    return {
      title: '',
      content: '',
      memory_type: 'note',
    };
  }

  async function refresh() {
    loading = true;
    error = null;
    try {
      const args = {};
      if (filterType) args.memory_type = filterType;
      if (filterSource) args.source = filterSource;
      memories = await listMemories(args);
    } catch (e) {
      error = e.message;
      memories = [];
    }
    loading = false;
  }

  function startCreate() {
    editing = null;
    form = blankForm();
    tagsText = '';
    view = 'edit';
  }

  async function startEdit(mem) {
    // Need content -- list doesn't include it
    try {
      const full = await getMemory(mem.id);
      editing = full;
      form = {
        title: full.title,
        content: full.content,
        memory_type: full.memory_type,
      };
      tagsText = (full.tags || []).join(', ');
      view = 'edit';
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function saveMemory() {
    const payload = {
      title: form.title,
      content: form.content,
      memory_type: form.memory_type,
    };
    payload.tags = tagsText.trim()
      ? tagsText.split(',').map(s => s.trim()).filter(Boolean)
      : [];

    try {
      if (editing) {
        await updateMemory(editing.id, payload);
        showToast('Memory updated');
      } else {
        await createMemory(payload);
        showToast('Memory saved');
      }
      view = 'list';
      await refresh();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function removeMemory(mem) {
    if (!confirm(`Delete memory '${mem.title}'?`)) return;
    try {
      await deleteMemory(mem.id);
      showToast('Memory deleted');
      await refresh();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function openSettings() {
    try {
      settings = await getUserSettings();
      view = 'settings';
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function saveSettings() {
    try {
      settings = await updateUserSettings({
        auto_extract_enabled: settings.auto_extract_enabled,
        auto_extract_model: settings.auto_extract_model,
        auto_extract_min_messages: settings.auto_extract_min_messages,
      });
      showToast('Settings saved');
      view = 'list';
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  function onFilterChange() {
    refresh();
  }
</script>

{#if open}
  <div class="memory-panel-overlay" onclick={close} role="presentation"></div>
  <div class="memory-panel">
    <div class="memory-panel-header">
      <span class="memory-panel-title">Memory</span>
      {#if view === 'list'}
        <button class="btn" onclick={startCreate}>+ New</button>
        <button class="btn" onclick={openSettings}>Settings</button>
      {:else}
        <button class="btn" onclick={() => { view = 'list'; refresh(); }}>&lt; Back</button>
      {/if}
      <button class="memory-panel-close" onclick={close}>&times;</button>
    </div>

    <div class="memory-body">
      {#if view === 'list'}
        <div class="filters">
          <label>
            Type
            <select bind:value={filterType} onchange={onFilterChange}>
              <option value="">all</option>
              {#each MEMORY_TYPES as t}
                <option value={t}>{t}</option>
              {/each}
            </select>
          </label>
          <label>
            Source
            <select bind:value={filterSource} onchange={onFilterChange}>
              <option value="">all</option>
              <option value="user">user</option>
              <option value="auto">auto-extracted</option>
            </select>
          </label>
        </div>

        {#if loading}
          <div class="empty">Loading...</div>
        {:else if error}
          <div class="empty error">Error: {error}</div>
        {:else if memories.length === 0}
          <div class="empty">No memories. Click "+ New" to add one.</div>
        {:else}
          {#each memories as m (m.id)}
            <div class="mem-card">
              <div class="mem-card-row">
                <span class="mem-card-title">{m.title}</span>
                <span class="mem-card-badge type-{m.memory_type}">{m.memory_type}</span>
                {#if m.source === 'auto'}
                  <span class="mem-card-badge auto">auto</span>
                {/if}
              </div>
              {#if m.tags && m.tags.length}
                <div class="mem-card-tags">
                  {#each m.tags as tag}
                    <span class="tag">{tag}</span>
                  {/each}
                </div>
              {/if}
              <div class="mem-card-actions">
                <button class="btn small" onclick={() => startEdit(m)}>Edit</button>
                <button class="btn small danger" onclick={() => removeMemory(m)}>Delete</button>
              </div>
            </div>
          {/each}
        {/if}

      {:else if view === 'edit'}
        <div class="form">
          <label>
            Title
            <input type="text" bind:value={form.title} placeholder="Brief title" />
          </label>
          <label>
            Type
            <select bind:value={form.memory_type}>
              {#each MEMORY_TYPES as t}
                <option value={t}>{t}</option>
              {/each}
            </select>
          </label>
          <label>
            Tags (comma-separated)
            <input type="text" bind:value={tagsText} placeholder="finance, volatility" />
          </label>
          <label>
            Content
            <textarea rows="10" bind:value={form.content} placeholder="The thing to remember..."></textarea>
          </label>
          {#if editing && editing.source === 'auto'}
            <div class="hint">
              Auto-extracted from conversation
              {#if editing.source_conversation_id}
                <code>{editing.source_conversation_id.slice(0, 8)}</code>
              {/if}
            </div>
          {/if}
          <div class="form-actions">
            <button class="btn" onclick={saveMemory}>{editing ? 'Update' : 'Create'}</button>
            <button class="btn" onclick={() => { view = 'list'; }}>Cancel</button>
          </div>
        </div>

      {:else if view === 'settings' && settings}
        <div class="form">
          <div class="settings-section">Auto-extract memories</div>
          <label class="inline">
            <input type="checkbox" bind:checked={settings.auto_extract_enabled} />
            <span>Enable automatic memory extraction from conversations</span>
          </label>
          <div class="hint">
            When enabled, a background job scans recent conversations and saves
            a handful of durable facts as "auto" memories. You can review and
            delete these from the list view.
          </div>
          <label>
            Extraction model
            <input type="text" bind:value={settings.auto_extract_model} />
          </label>
          <label>
            Minimum messages before extracting
            <input type="number" min="1" max="100" bind:value={settings.auto_extract_min_messages} />
          </label>
          <div class="form-actions">
            <button class="btn" onclick={saveSettings}>Save</button>
            <button class="btn" onclick={() => { view = 'list'; }}>Cancel</button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .memory-panel-overlay {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.4);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
    z-index: 299;
    animation: overlay-in 0.2s ease;
  }
  @keyframes overlay-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .memory-panel {
    position: fixed;
    right: 0; top: 0; bottom: 0;
    width: 460px; max-width: 100%;
    background: var(--surface);
    border-left: 1px solid var(--border);
    z-index: 300;
    display: flex; flex-direction: column;
    animation: slide-in 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    font-family: var(--font-ui);
    box-shadow: -8px 0 32px rgba(0,0,0,0.2);
    overflow: hidden;
  }
  @keyframes slide-in {
    from { transform: translateX(100%); }
    to { transform: translateX(0); }
  }

  .memory-panel-header {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px;
  }
  .memory-panel-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: 0.05em;
    flex: 1;
  }
  .memory-panel-close {
    background: transparent; border: none;
    color: var(--fg-dim); font-size: 20px; cursor: pointer;
    padding: 0 6px;
  }
  .memory-panel-close:hover { color: var(--fg-bright); }

  .memory-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
    font-size: 13px;
  }

  .filters {
    display: flex; gap: 12px;
    margin-bottom: 10px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
  }
  .filters label {
    display: flex;
    flex-direction: column;
    gap: 3px;
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .filters select {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--fg-bright);
    padding: 4px 6px;
    font-size: 12px;
    min-width: 90px;
  }

  .empty {
    padding: 24px 0;
    text-align: center;
    color: var(--muted);
  }
  .empty.error { color: var(--red); }

  .mem-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px;
    margin-bottom: 10px;
  }
  .mem-card-row {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 4px;
    flex-wrap: wrap;
  }
  .mem-card-title {
    font-weight: 600;
    color: var(--fg-bright);
    flex: 1;
    word-break: break-word;
  }
  .mem-card-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--border);
    color: var(--fg-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .mem-card-badge.auto {
    background: var(--accent-soft);
    color: var(--fg-bright);
  }
  .mem-card-tags {
    display: flex; gap: 4px; flex-wrap: wrap;
    margin-bottom: 8px;
  }
  .tag {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--border2);
    color: var(--fg-dim);
  }
  .mem-card-actions {
    display: flex; gap: 6px;
  }

  .btn {
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 3px;
    color: var(--fg-dim);
    padding: 4px 10px;
    font-family: var(--font-ui);
    font-size: 12px;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }
  .btn:hover {
    background: var(--border);
    color: var(--fg-bright);
  }
  .btn.small { padding: 3px 8px; font-size: 11px; }
  .btn.danger { color: var(--red); }
  .btn.danger:hover { background: rgba(255,0,0,0.08); }

  .form {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .form label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.03em;
    text-transform: uppercase;
  }
  .form label.inline {
    flex-direction: row;
    align-items: center;
    gap: 8px;
    text-transform: none;
    font-size: 13px;
    color: var(--fg-dim);
    letter-spacing: 0;
  }
  .form input, .form textarea, .form select {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--fg-bright);
    padding: 6px 8px;
    font-family: var(--font-mono);
    font-size: 12px;
    text-transform: none;
    letter-spacing: 0;
  }
  .form textarea {
    resize: vertical;
    min-height: 140px;
  }
  .form-actions { display: flex; gap: 8px; margin-top: 8px; }

  .settings-section {
    font-size: 11px;
    color: var(--fg-bright);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: -4px;
  }
  .hint {
    font-size: 11px;
    color: var(--muted);
    line-height: 1.5;
  }
  .hint code {
    font-family: var(--font-mono);
    background: var(--border);
    padding: 0 4px;
    border-radius: 2px;
  }
</style>
