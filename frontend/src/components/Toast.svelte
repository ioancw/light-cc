<script>
  import { appState } from '../state.svelte.js';

  // Toasts are stored in appState.toasts (array of { id, message, type })
  // This component renders and auto-dismisses them.

  function dismiss(id) {
    appState.toasts = appState.toasts.filter(t => t.id !== id);
  }
</script>

{#if appState.toasts && appState.toasts.length > 0}
  <div class="toast-container">
    {#each appState.toasts as toast (toast.id)}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="toast" class:error={toast.type === 'error'} class:success={toast.type === 'success'} onclick={() => dismiss(toast.id)}>
        {toast.message}
      </div>
    {/each}
  </div>
{/if}

<style>
  .toast-container {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 6000;
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: center;
    pointer-events: none;
  }

  .toast {
    background: var(--surface2);
    border: 1px solid var(--border2);
    color: var(--fg);
    padding: 8px 20px;
    border-radius: 6px;
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.04em;
    box-shadow: 0 4px 16px rgba(0,0,0,0.3);
    animation: toast-in 0.2s ease;
    pointer-events: auto;
    cursor: pointer;
    max-width: 400px;
    text-align: center;
  }

  .toast.error {
    border-color: var(--red);
    color: var(--red);
  }

  .toast.success {
    border-color: var(--green);
    color: var(--green);
  }

  @keyframes toast-in {
    from { opacity: 0; transform: translateY(10px); }
    to   { opacity: 1; transform: translateY(0); }
  }
</style>
