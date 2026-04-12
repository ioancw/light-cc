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

  let currentTokens = $derived(
    appState.currentId && appState.conversations[appState.currentId]
      ? (appState.conversations[appState.currentId].totalTokens || 0)
      : 0
  );
</script>

<div class="status-bar">
  <div class="connection">
    <div class="status-dot" style:background={dotColor} style:box-shadow="0 0 6px {dotColor}"></div>
    <span class="status-text {statusClass}">{statusText}</span>
  </div>
  <span class="status-sep"></span>
  <div class="token-count">
    <span class="token-value">{formatTokens(currentTokens)}</span> tokens
  </div>
</div>

<style>
  .status-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0;
    flex-shrink: 0;
    font-size: 12px;
    font-family: var(--font-ui);
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
    letter-spacing: 0.01em;
  }
  .status-text.connected { color: var(--green); }
  .status-text.connecting { color: var(--amber); }
  .status-text.disconnected { color: var(--red); }

  .status-sep {
    width: 1px;
    height: 12px;
    background: var(--border2);
    flex-shrink: 0;
  }

  .token-count {
    color: var(--muted);
    letter-spacing: 0.05em;
  }
  .token-value {
    color: var(--accent-soft);
    font-weight: 600;
  }
</style>
