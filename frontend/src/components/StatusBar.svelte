<script>
  import { appState } from '../state.svelte.js';
  import { formatTokens } from '../lib/utils.js';

  let statusText = $derived(
    appState.connected ? 'connected' :
    appState.connecting ? 'connecting...' :
    'disconnected'
  );

  let statusClass = $derived(
    appState.connected ? 'connected' :
    appState.connecting ? 'connecting' :
    'disconnected'
  );

  let dotColor = $derived(
    appState.connected ? 'var(--green)' :
    appState.connecting ? 'var(--amber)' :
    'var(--red)'
  );
</script>

<div class="status-bar">
  <div class="connection">
    <div class="status-dot" style:background={dotColor} style:box-shadow="0 0 6px {dotColor}"></div>
    <span class="status-text {statusClass}">{statusText}</span>
  </div>
  <div class="token-count">
    <span class="token-value">{formatTokens(appState.totalTokens)}</span> tokens
  </div>
</div>

<style>
  .status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    height: 28px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
    font-size: 11px;
  }

  .connection {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .status-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .status-text {
    letter-spacing: 0.08em;
  }
  .status-text.connected { color: var(--green); }
  .status-text.connecting { color: var(--amber); }
  .status-text.disconnected { color: var(--red); }

  .token-count {
    color: var(--muted);
    letter-spacing: 0.05em;
  }
  .token-value {
    color: var(--accent-soft);
  }
</style>
