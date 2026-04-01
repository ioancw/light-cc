<script>
  import { login, register } from '../api.js';

  let activeTab = $state('login');
  let loading = $state(false);
  let error = $state('');

  // Form fields
  let loginEmail = $state('');
  let loginPassword = $state('');
  let regName = $state('');
  let regEmail = $state('');
  let regPassword = $state('');

  async function handleLogin(e) {
    e.preventDefault();
    error = '';
    loading = true;
    try {
      await login(loginEmail, loginPassword);
    } catch (err) {
      error = err.message;
    } finally {
      loading = false;
    }
  }

  async function handleRegister(e) {
    e.preventDefault();
    error = '';
    loading = true;
    try {
      await register(regName, regEmail, regPassword);
    } catch (err) {
      error = err.message;
    } finally {
      loading = false;
    }
  }
</script>

<div class="auth-page">
  <div class="auth-card">
    <div class="auth-logo">
      <h1>&#9670; Light CC</h1>
      <p>AI-powered workspace</p>
    </div>

    <div class="tabs">
      <button
        class="tab"
        class:active={activeTab === 'login'}
        onclick={() => { activeTab = 'login'; error = ''; }}
      >Sign In</button>
      <button
        class="tab"
        class:active={activeTab === 'register'}
        onclick={() => { activeTab = 'register'; error = ''; }}
      >Register</button>
    </div>

    {#if activeTab === 'login'}
      <form onsubmit={handleLogin}>
        <div class="form-group">
          <label for="login-email">Email</label>
          <input type="email" id="login-email" bind:value={loginEmail} required autocomplete="email">
        </div>
        <div class="form-group">
          <label for="login-password">Password</label>
          <input type="password" id="login-password" bind:value={loginPassword} required autocomplete="current-password">
        </div>
        <button type="submit" class="submit-btn" class:loading disabled={loading}>Sign In</button>
      </form>
    {:else}
      <form onsubmit={handleRegister}>
        <div class="form-group">
          <label for="reg-name">Display Name</label>
          <input type="text" id="reg-name" bind:value={regName} required autocomplete="name">
        </div>
        <div class="form-group">
          <label for="reg-email">Email</label>
          <input type="email" id="reg-email" bind:value={regEmail} required autocomplete="email">
        </div>
        <div class="form-group">
          <label for="reg-password">Password</label>
          <input type="password" id="reg-password" bind:value={regPassword} required minlength="6" autocomplete="new-password">
        </div>
        <button type="submit" class="submit-btn" class:loading disabled={loading}>Create Account</button>
      </form>
    {/if}

    {#if error}
      <div class="error-msg">{error}</div>
    {/if}
  </div>
</div>

<style>
  .auth-page {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    background: var(--bg);
    position: relative;
    overflow: hidden;
  }
  .auth-page::before {
    content: '';
    position: absolute;
    width: 500px; height: 500px;
    background: radial-gradient(circle, var(--accent-glow) 0%, transparent 70%);
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    pointer-events: none;
    opacity: 0.6;
  }

  .auth-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 44px;
    width: 100%;
    max-width: 400px;
    position: relative;
    z-index: 1;
    box-shadow: 0 8px 40px rgba(0,0,0,0.15);
    animation: auth-in 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  }
  @keyframes auth-in {
    from { opacity: 0; transform: translateY(12px) scale(0.98); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }

  .auth-logo {
    text-align: center;
    margin-bottom: 36px;
  }
  .auth-logo h1 {
    font-family: 'Lora', serif;
    font-size: 24px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--fg-bright);
  }
  .auth-logo p {
    color: var(--fg-dim);
    font-size: 13px;
    margin-top: 8px;
    letter-spacing: 0.06em;
  }

  .tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
  }
  .tab {
    flex: 1;
    padding: 10px;
    text-align: center;
    font-size: 13px;
    font-weight: 500;
    color: var(--muted);
    cursor: pointer;
    border: none;
    background: none;
    border-bottom: 2px solid transparent;
    transition: all 0.2s;
    font-family: 'Geist Mono', monospace;
  }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .form-group {
    margin-bottom: 16px;
  }
  .form-group label {
    display: block;
    font-size: 12px;
    font-weight: 500;
    color: var(--fg-dim);
    margin-bottom: 6px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .form-group input {
    width: 100%;
    padding: 10px 12px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 6px;
    color: var(--fg);
    font-family: 'Geist Mono', monospace;
    font-size: 14px;
    outline: none;
    transition: border-color 0.2s;
  }
  .form-group input:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow);
  }

  .submit-btn {
    width: 100%;
    padding: 11px;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 6px;
    font-family: 'Geist Mono', monospace;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    margin-top: 8px;
    transition: background 0.2s;
    position: relative;
  }
  .submit-btn:hover { background: var(--accent-soft); }
  .submit-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .submit-btn.loading { color: transparent; }
  .submit-btn.loading::after {
    content: '';
    position: absolute;
    width: 16px; height: 16px;
    top: 50%; left: 50%;
    margin: -8px 0 0 -8px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .error-msg {
    color: var(--red);
    font-size: 12px;
    margin-top: 12px;
    text-align: center;
  }
</style>
