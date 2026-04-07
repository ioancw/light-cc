<script>
  import { appState, clearAuth } from '../state.svelte.js';
  import { logout } from '../api.js';
  import { THEMES, setTheme } from '../theme.js';

  let { open = $bindable(false) } = $props();

  function close() {
    open = false;
  }

  const shortcuts = [
    { keys: 'Ctrl+B', action: 'Toggle sidebar' },
    { keys: 'Ctrl+K', action: 'New conversation' },
    { keys: 'Enter', action: 'Send message' },
    { keys: 'Shift+Enter', action: 'New line' },
    { keys: 'Escape', action: 'Dismiss dialog' },
    { keys: '/', action: 'Slash commands' },
  ];
</script>

{#if open}
  <div class="settings-overlay" onclick={close} role="presentation"></div>
  <div class="settings-panel" role="dialog" aria-modal="true" aria-label="Settings">
    <div class="settings-header">
      <span class="settings-title">Settings</span>
      <button class="settings-close" onclick={close} aria-label="Close settings">&times;</button>
    </div>

    <div class="settings-body">
      <!-- Profile -->
      <section class="settings-section">
        <h3 class="section-heading">Profile</h3>
        <div class="profile-row">
          <div class="profile-avatar">{appState.user?.name?.[0]?.toUpperCase() || 'U'}</div>
          <div class="profile-info">
            <span class="profile-name">{appState.user?.name || 'User'}</span>
            <span class="profile-email">{appState.user?.email || ''}</span>
          </div>
        </div>
      </section>

      <!-- Theme -->
      <section class="settings-section">
        <h3 class="section-heading">Theme</h3>
        <div class="theme-grid" role="radiogroup" aria-label="Color theme">
          {#each THEMES as t (t.name)}
            <button
              class="theme-card"
              class:active={appState.theme === t.name}
              role="radio"
              aria-label="{t.label} theme"
              aria-checked={appState.theme === t.name}
              onclick={() => setTheme(t.name)}
            >
              <div class="theme-preview" style:background={t.color}></div>
              <span class="theme-name">{t.label}</span>
            </button>
          {/each}
        </div>
      </section>

      <!-- Keyboard Shortcuts -->
      <section class="settings-section">
        <h3 class="section-heading">Keyboard Shortcuts</h3>
        <div class="shortcuts-list">
          {#each shortcuts as s}
            <div class="shortcut-row">
              <span class="shortcut-keys"><kbd>{s.keys}</kbd></span>
              <span class="shortcut-action">{s.action}</span>
            </div>
          {/each}
        </div>
      </section>

      <!-- Actions -->
      <section class="settings-section">
        <button class="settings-logout" onclick={logout}>Sign Out</button>
      </section>
    </div>
  </div>
{/if}

<style>
  .settings-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.4);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
    z-index: 399;
    animation: overlay-in 0.2s ease;
  }
  @keyframes overlay-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .settings-panel {
    position: fixed;
    right: 0; top: 0; bottom: 0;
    width: 360px;
    max-width: 100%;
    background: var(--surface);
    border-left: 1px solid var(--border);
    z-index: 400;
    display: flex;
    flex-direction: column;
    animation: slide-in 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: -8px 0 32px rgba(0,0,0,0.2);
  }
  @keyframes slide-in {
    from { transform: translateX(100%); }
    to { transform: translateX(0); }
  }

  .settings-header {
    padding: 18px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .settings-title {
    font-family: var(--font-ui);
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
    letter-spacing: 0.02em;
  }

  .settings-close {
    background: none;
    border: none;
    color: var(--muted);
    font-size: 18px;
    cursor: pointer;
    padding: 2px 6px;
    transition: color 0.12s;
    line-height: 1;
  }
  .settings-close:hover { color: var(--fg); }

  .settings-body {
    flex: 1;
    overflow-y: auto;
    padding: 8px 0;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  .settings-section {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
  }
  .settings-section:last-child { border-bottom: none; }

  .section-heading {
    font-family: var(--font-ui);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: var(--muted);
    margin-bottom: 12px;
  }

  /* Profile */
  .profile-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .profile-avatar {
    width: 36px; height: 36px;
    border-radius: 8px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    display: flex; align-items: center; justify-content: center;
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-dim);
    font-family: var(--font-ui);
  }

  .profile-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .profile-name {
    font-family: var(--font-ui);
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-bright);
  }

  .profile-email {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--muted);
  }

  /* Theme */
  .theme-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
    gap: 8px;
  }

  .theme-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 10px 8px;
    border-radius: 8px;
    border: 1px solid var(--border2);
    background: var(--bg);
    cursor: pointer;
    transition: all 0.15s ease;
    font: inherit;
    color: inherit;
  }
  .theme-card:hover {
    border-color: var(--muted);
    background: var(--surface2);
  }
  .theme-card.active {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px var(--accent-glow);
  }

  .theme-preview {
    width: 32px; height: 32px;
    border-radius: 50%;
    border: 2px solid rgba(255,255,255,0.1);
    flex-shrink: 0;
  }

  .theme-name {
    font-family: var(--font-ui);
    font-size: 11px;
    color: var(--fg-dim);
    font-weight: 500;
  }
  .theme-card.active .theme-name {
    color: var(--accent-soft);
  }

  /* Shortcuts */
  .shortcuts-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .shortcut-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 0;
  }

  .shortcut-keys kbd {
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 4px;
    padding: 2px 8px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--fg-dim);
  }

  .shortcut-action {
    font-family: var(--font-ui);
    font-size: 12px;
    color: var(--muted);
  }

  /* Logout */
  .settings-logout {
    width: 100%;
    padding: 9px 12px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    color: var(--fg-dim);
    font-family: var(--font-ui);
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
    text-align: center;
  }
  .settings-logout:hover {
    border-color: var(--red);
    color: var(--red);
    background: var(--red-soft);
  }
</style>
