<script>
  import { onMount, onDestroy } from 'svelte';
  import { showToast, appState, switchConversation } from '../state.svelte.js';
  import { send } from '../ws.js';
  import { fetchConversationHistory } from '../api.js';
  import {
    listAgents, getAgent, createAgent, updateAgent, deleteAgent,
    runAgent, listAgentRuns,
  } from '../api.js';

  let open = $state(false);
  let view = $state('list');  // 'list' | 'edit' | 'runs'
  let agents = $state([]);
  let loading = $state(false);
  let error = $state(null);

  // Edit form state
  let editing = $state(null);     // current agent being edited (or null for new)
  let form = $state(blankForm());
  let toolsText = $state('');     // comma-separated

  // Runs view
  let runsForAgent = $state(null);
  let runs = $state([]);

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
      name: '',
      description: '',
      system_prompt: '',
      model: null,
      tools: null,
      max_turns: 20,
      timeout_seconds: 300,
      memory_scope: 'user',
      enabled: true,
    };
  }

  async function refresh() {
    loading = true;
    error = null;
    try {
      agents = await listAgents();
    } catch (e) {
      error = e.message;
      agents = [];
    }
    loading = false;
  }

  function startCreate() {
    editing = null;
    form = blankForm();
    toolsText = '';
    view = 'edit';
  }

  function startEdit(agent) {
    editing = agent;
    form = { ...agent };
    toolsText = (agent.tools || []).join(', ');
    view = 'edit';
  }

  async function saveAgent() {
    const payload = { ...form };
    payload.tools = toolsText.trim()
      ? toolsText.split(',').map(s => s.trim()).filter(Boolean)
      : null;
    if (!payload.model) payload.model = null;

    try {
      if (editing) {
        await updateAgent(editing.id, payload);
        showToast('Agent updated');
      } else {
        await createAgent(payload);
        showToast('Agent created');
      }
      view = 'list';
      await refresh();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function removeAgent(agent) {
    if (!confirm(`Delete agent '${agent.name}'?`)) return;
    try {
      await deleteAgent(agent.id);
      showToast('Agent deleted');
      await refresh();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function triggerRun(agent) {
    try {
      const run = await runAgent(agent.id);
      showToast(`Run started: ${run.id.slice(0, 8)}`);
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function toggleEnabled(agent) {
    try {
      await updateAgent(agent.id, { enabled: !agent.enabled });
      await refresh();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function viewRuns(agent) {
    runsForAgent = agent;
    runs = [];
    view = 'runs';
    try {
      runs = await listAgentRuns(agent.id, 20);
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function onAgentResult() {
    // A run just finished — refresh whichever view is open so token counts,
    // statuses, and last-run timestamps update without requiring a manual reload.
    if (view === 'runs' && runsForAgent) {
      try { runs = await listAgentRuns(runsForAgent.id, 20); } catch {}
    } else if (view === 'list') {
      try { agents = await listAgents(); } catch {}
    }
  }

  onMount(() => { window.addEventListener('agent_result', onAgentResult); });
  onDestroy(() => { window.removeEventListener('agent_result', onAgentResult); });

  function fmtDate(iso) {
    if (!iso) return '-';
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  }

  async function openRunConversation(run) {
    const convId = run.conversation_id;
    if (!convId) {
      showToast('No conversation linked to this run yet', 'error');
      return;
    }
    // Make sure the local conversation map knows about it
    await fetchConversationHistory();
    let localId = null;
    for (const [id, c] of Object.entries(appState.conversations)) {
      if (c.serverId === convId) { localId = id; break; }
    }
    if (!localId) {
      // Create a stub locally and let resume_conversation populate it
      localId = 'conv_' + Date.now();
      appState.conversations[localId] = {
        id: localId,
        serverId: convId,
        title: `[Agent] ${runsForAgent?.name || ''}`,
        messages: [],
        createdAt: Date.now(),
        titleGenerated: true,
        totalTokens: 0,
        stub: true,
      };
    }
    switchConversation(localId);
    send('resume_conversation', { conversation_id: convId }, localId);
    open = false;
  }

  function truncate(s, n = 180) {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '...' : s;
  }
</script>

{#if open}
  <div class="agent-panel-overlay" onclick={close} role="presentation"></div>
  <div class="agent-panel">
    <div class="agent-panel-header">
      <span class="agent-panel-title">Agents</span>
      {#if view === 'list'}
        <button class="btn" onclick={startCreate}>+ New</button>
      {:else}
        <button class="btn" onclick={() => { view = 'list'; refresh(); }}>&lt; Back</button>
      {/if}
      <button class="agent-panel-close" onclick={close}>&times;</button>
    </div>

    <div class="agent-body">
      {#if view === 'list'}
        {#if loading}
          <div class="empty">Loading...</div>
        {:else if error}
          <div class="empty error">Error: {error}</div>
        {:else if agents.length === 0}
          <div class="empty">No agents defined. Click "+ New" to create one.</div>
        {:else}
          {#each agents as a (a.id)}
            <div class="agent-card" class:disabled={!a.enabled}>
              <div class="agent-card-row">
                <span class="agent-card-name">{a.name}</span>
                {#if a.source === 'yaml'}<span class="agent-card-badge yaml">yaml</span>
                {:else if a.source && a.source.startsWith('plugin:')}<span class="agent-card-badge yaml">{a.source}</span>{/if}
              </div>
              <div class="agent-card-desc">{a.description}</div>
              <div class="agent-card-meta">
                {#if a.last_run_at}last run: {fmtDate(a.last_run_at)}{/if}
              </div>
              <div class="agent-card-actions">
                <button class="btn small" onclick={() => triggerRun(a)} disabled={!a.enabled}>Run</button>
                <button class="btn small" onclick={() => viewRuns(a)}>Runs</button>
                <button class="btn small" onclick={() => startEdit(a)}>Edit</button>
                <button class="btn small" onclick={() => toggleEnabled(a)}>
                  {a.enabled ? 'Disable' : 'Enable'}
                </button>
                <button class="btn small danger" onclick={() => removeAgent(a)}>Delete</button>
              </div>
            </div>
          {/each}
        {/if}

      {:else if view === 'edit'}
        <div class="form">
          <label>
            Name
            <input type="text" bind:value={form.name} placeholder="morning-briefing" />
          </label>
          <label>
            Description
            <input type="text" bind:value={form.description} placeholder="Short one-line summary" />
          </label>
          <label>
            System prompt
            <textarea rows="8" bind:value={form.system_prompt} placeholder="You are a ..."></textarea>
          </label>
          <label>
            Model (optional)
            <select bind:value={form.model}>
              <option value={null}>-- inherit default --</option>
              {#each appState.availableModels as m}
                <option value={m}>{m}</option>
              {/each}
            </select>
          </label>
          <label>
            Tools (comma-separated, empty = all)
            <input type="text" bind:value={toolsText} placeholder="WebSearch, WebFetch, Write" />
          </label>
          <div class="form-row">
            <label>
              Max turns
              <input type="number" min="1" max="100" bind:value={form.max_turns} />
            </label>
            <label>
              Timeout (s)
              <input type="number" min="10" max="3600" bind:value={form.timeout_seconds} />
            </label>
            <label>
              Memory scope
              <select bind:value={form.memory_scope}>
                <option value="user">user</option>
                <option value="agent">agent</option>
                <option value="none">none</option>
              </select>
            </label>
          </div>
          <div class="form-actions">
            <button class="btn" onclick={saveAgent}>{editing ? 'Update' : 'Create'}</button>
            <button class="btn" onclick={() => { view = 'list'; }}>Cancel</button>
          </div>
        </div>

      {:else if view === 'runs'}
        <div class="runs-header">
          Runs for <strong>{runsForAgent?.name}</strong>
        </div>
        {#if runs.length === 0}
          <div class="empty">No runs yet.</div>
        {:else}
          {#each runs as r (r.id)}
            <div class="run-card">
              <div class="run-row">
                <span class="run-status run-{r.status}">{r.status}</span>
                <span class="run-time">{fmtDate(r.started_at)}</span>
                <span class="run-trigger">{r.trigger_type}</span>
                <span class="run-tokens">{r.tokens_used} tok</span>
              </div>
              {#if r.result}
                <div class="run-preview">{truncate(r.result)}</div>
              {:else if r.error}
                <div class="run-preview error">{truncate(r.error)}</div>
              {/if}
              <div class="run-actions">
                <button
                  class="btn small"
                  onclick={() => openRunConversation(r)}
                  disabled={!r.conversation_id}
                  title={r.conversation_id ? 'Open full transcript' : 'No transcript yet'}
                >View</button>
              </div>
            </div>
          {/each}
        {/if}
      {/if}
    </div>
  </div>
{/if}

<style>
  .agent-panel-overlay {
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

  .agent-panel {
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

  .agent-panel-header {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 10px;
  }
  .agent-panel-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: 0.05em;
    flex: 1;
  }
  .agent-panel-close {
    background: transparent; border: none;
    color: var(--fg-dim); font-size: 20px; cursor: pointer;
    padding: 0 6px;
  }
  .agent-panel-close:hover { color: var(--fg-bright); }

  .agent-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
    font-size: 13px;
  }

  .empty {
    padding: 24px 0;
    text-align: center;
    color: var(--muted);
  }
  .empty.error { color: var(--red); }

  .agent-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px;
    margin-bottom: 10px;
  }
  .agent-card.disabled { opacity: 0.55; }
  .agent-card-row {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 4px;
  }
  .agent-card-name {
    font-weight: 600;
    color: var(--fg-bright);
  }
  .agent-card-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--border);
    color: var(--fg-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .agent-card-badge.yaml { background: var(--border2); }
  .agent-card-desc {
    color: var(--fg-dim);
    margin-bottom: 6px;
    font-size: 12px;
  }
  .agent-card-meta {
    color: var(--muted);
    font-size: 11px;
    margin-bottom: 8px;
  }
  .agent-card-actions {
    display: flex; gap: 6px; flex-wrap: wrap;
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
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }

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
    min-height: 120px;
  }
  .form-row { display: flex; gap: 8px; }
  .form-row label { flex: 1; }
  .form-actions { display: flex; gap: 8px; margin-top: 8px; }

  .runs-header {
    margin-bottom: 10px;
    color: var(--fg-bright);
    font-size: 12px;
  }
  .run-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    margin-bottom: 8px;
  }
  .run-row {
    display: grid;
    grid-template-columns: 80px 1fr 70px 70px;
    gap: 8px;
    font-size: 12px;
    color: var(--fg-dim);
    align-items: center;
  }
  .run-preview {
    margin-top: 6px;
    padding: 6px 8px;
    background: var(--bg);
    border-radius: 3px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--fg-dim);
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 90px;
    overflow-y: auto;
  }
  .run-preview.error { color: var(--red); }
  .run-actions {
    display: flex; gap: 6px;
    margin-top: 6px;
  }
  .run-status {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 3px;
    text-align: center;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .run-completed { background: var(--accent-soft); color: var(--fg-bright); }
  .run-failed { background: rgba(255,0,0,0.15); color: var(--red); }
  .run-running { background: var(--border); color: var(--fg-dim); }
  .run-time { color: var(--muted); font-size: 11px; }
  .run-trigger { color: var(--muted); font-size: 11px; }
  .run-tokens { color: var(--muted); font-size: 11px; text-align: right; }
</style>
