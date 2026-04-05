<script>
  import { appState, pendingPermission } from '../state.svelte.js';
  import { send } from '../ws.js';

  let denyBtnEl = $state(null);

  // Reactive: permission for the currently-viewed conversation
  let perm = $derived(pendingPermission());

  $effect(() => {
    if (perm && denyBtnEl) {
      denyBtnEl.focus();
    }
  });

  function allow() {
    if (!perm) return;
    send('permission_response', { request_id: perm.requestId, allowed: true }, perm.cid);
    delete appState.pendingPermissions[perm.cid];
  }

  function deny() {
    if (!perm) return;
    send('permission_response', { request_id: perm.requestId, allowed: false }, perm.cid);
    delete appState.pendingPermissions[perm.cid];
  }
</script>

<svelte:window onkeydown={(e) => { if (perm && e.key === 'Escape') deny(); }} />

{#if perm}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="permission-overlay">
    <div class="permission-dialog" role="dialog" aria-modal="true" aria-labelledby="permissionTitle">
      <div class="permission-title" id="permissionTitle">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M8 1L1 14h14L8 1z" stroke="currentColor" stroke-width="1.5" fill="none"/>
          <path d="M8 6v4M8 11.5v.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        Tool Permission Required
      </div>
      <div class="permission-body">
        <strong>{perm.toolName}</strong> wants to run: {perm.summary}
      </div>
      <div class="permission-actions">
        <button class="permission-btn deny" bind:this={denyBtnEl} onclick={deny}>Deny</button>
        <button class="permission-btn allow" onclick={allow}>Allow</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .permission-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    z-index: 5000;
    display: flex;
    align-items: center;
    justify-content: center;
    animation: fade-in 0.2s ease;
  }
  @keyframes fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .permission-dialog {
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: 12px;
    padding: 28px;
    max-width: 460px;
    width: 90%;
    animation: dialog-in 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: 0 16px 48px rgba(0,0,0,0.3);
  }
  @keyframes dialog-in {
    from { opacity: 0; transform: translateY(8px) scale(0.97); }
    to   { opacity: 1; transform: translateY(0) scale(1); }
  }

  .permission-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--amber);
    letter-spacing: 0.05em;
    margin-bottom: 14px;
    display: flex; align-items: center; gap: 8px;
  }

  .permission-body {
    font-size: 12px;
    color: var(--fg);
    line-height: 1.7;
    margin-bottom: 20px;
    font-family: 'Geist Mono', monospace;
    padding: 12px;
    background: var(--surface2);
    border-radius: 4px;
    border: 1px solid var(--border);
    word-break: break-all;
  }

  .permission-actions {
    display: flex; gap: 10px; justify-content: flex-end;
  }

  .permission-btn {
    padding: 8px 20px;
    border-radius: 4px;
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.06em;
    transition: all 0.15s;
    border: 1px solid;
  }
  .permission-btn.allow {
    background: var(--green-soft);
    border-color: var(--green);
    color: var(--green);
  }
  .permission-btn.allow:hover { background: var(--green); color: #fff; }
  .permission-btn.deny {
    background: var(--red-soft);
    border-color: var(--red);
    color: var(--red);
  }
  .permission-btn.deny:hover { background: var(--red); color: #fff; }
</style>
