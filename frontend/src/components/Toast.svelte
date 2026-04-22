<script>
  import { appState } from '../state.svelte.js';

  // Toasts are stored in appState.toasts (array of { id, message, type })
  // This component renders and auto-dismisses them.

  let exiting = $state(new Set());

  function dismiss(id) {
    exiting = new Set([...exiting, id]);
    setTimeout(() => {
      appState.toasts = appState.toasts.filter(t => t.id !== id);
      const next = new Set(exiting);
      next.delete(id);
      exiting = next;
    }, 180);
  }
</script>

{#if appState.toasts && appState.toasts.length > 0}
  <div class="toast-container">
    {#each appState.toasts as toast (toast.id)}
      <button
        class="toast"
        class:error={toast.type === 'error'}
        class:success={toast.type === 'success'}
        class:exiting={exiting.has(toast.id)}
        onclick={() => dismiss(toast.id)}
        role={toast.type === 'error' ? 'alert' : 'status'}
        aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
        aria-label="{toast.message} (click to dismiss)"
      >
        {toast.message}
      </button>
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
    padding: 10px 24px;
    border-radius: 8px;
    font-family: var(--font-ui);
    font-size: 11px;
    letter-spacing: 0.04em;
    box-shadow: 0 8px 32px rgba(0,0,0,0.25);
    animation: toast-in 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: auto;
    cursor: pointer;
    max-width: 400px;
    text-align: center;
    transition: opacity 0.18s ease, transform 0.18s ease;
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }

  .toast.exiting {
    opacity: 0;
    transform: translateY(8px);
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
