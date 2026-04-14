<script>
  import { currentConversation, appState, isConversationLoading } from '../state.svelte.js';
  import MessageBubble from './MessageBubble.svelte';

  let messagesEl = $state(null);
  let autoScroll = $state(true);

  function useSuggestion(prompt) {
    window.dispatchEvent(new CustomEvent('lcc-suggestion', { detail: { prompt } }));
  }

  // Track message count to trigger auto-scroll
  let messageCount = $derived(currentConversation()?.messages?.length || 0);
  let lastStreamContent = $derived.by(() => {
    const msgs = currentConversation()?.messages;
    if (!msgs || msgs.length === 0) return '';
    const last = msgs[msgs.length - 1];
    return last.streaming ? last.content : '';
  });

  $effect(() => {
    // Trigger on message count changes or streaming content updates
    messageCount;
    lastStreamContent;
    if (autoScroll && messagesEl) {
      requestAnimationFrame(() => {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      });
    }
  });

  function onScroll() {
    if (!messagesEl) return;
    const atBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
    autoScroll = atBottom;
  }

  function scrollToBottom() {
    if (messagesEl) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
      autoScroll = true;
    }
  }

  // Expose scroll state and function to InputBar via appState
  $effect(() => {
    appState.needsScrollDown = !autoScroll;
    appState.scrollToBottom = scrollToBottom;
  });
</script>

<div class="messages" bind:this={messagesEl} onscroll={onScroll}>
  {#if isConversationLoading(appState.currentId)}
    <div class="loading-skeleton">
      <div class="skeleton-msg skeleton-user"><div class="skeleton-bar" style="width: 55%"></div></div>
      <div class="skeleton-msg skeleton-assistant">
        <div class="skeleton-bar" style="width: 80%"></div>
        <div class="skeleton-bar" style="width: 65%"></div>
        <div class="skeleton-bar" style="width: 40%"></div>
      </div>
      <div class="skeleton-msg skeleton-user"><div class="skeleton-bar" style="width: 35%"></div></div>
      <div class="skeleton-msg skeleton-assistant">
        <div class="skeleton-bar" style="width: 70%"></div>
        <div class="skeleton-bar" style="width: 50%"></div>
      </div>
    </div>
  {:else if !currentConversation() || currentConversation().messages.length === 0}
    <div class="empty-state">
      <div class="empty-mark">
        <span class="empty-bracket">[</span>
        <span class="empty-dot"></span>
        <span class="empty-bracket">]</span>
      </div>
      <h2 class="empty-title">
        <span class="empty-title-line">A quiet workbench</span>
        <span class="empty-title-line empty-title-accent">for thinking with tools.</span>
      </h2>
      <p class="empty-meta">
        <span>code</span><span class="empty-sep">/</span>
        <span>analysis</span><span class="empty-sep">/</span>
        <span>plots</span><span class="empty-sep">/</span>
        <span>prose</span>
      </p>
      {#if appState.suggestions.length > 0}
        <div class="suggestions">
          {#each appState.suggestions as s}
            <button class="suggestion-chip" onclick={() => useSuggestion(s.prompt)}>
              {s.label}
            </button>
          {/each}
        </div>
      {:else}
        <button class="empty-cta" onclick={() => document.querySelector('.input-textarea')?.focus()}>
          <span class="empty-cta-kbd">↵</span>
          Begin
        </button>
      {/if}
    </div>
  {:else}
    {#each currentConversation().messages as msg (msg.id)}
      <MessageBubble {msg} />
    {/each}
  {/if}
</div>


<style>
  .messages {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 0;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: 22px;
    padding: 40px;
    text-align: center;
  }

  .empty-mark {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    font-family: var(--font-mono);
    font-size: 24px;
    color: var(--muted);
    animation: empty-mark-in 0.6s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  .empty-bracket {
    line-height: 1;
    font-weight: 300;
  }
  .empty-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 16px var(--accent-glow);
    animation: empty-dot-pulse 2.4s ease-in-out infinite;
  }
  @keyframes empty-mark-in {
    from { opacity: 0; transform: translateY(6px); letter-spacing: 0.3em; }
    to { opacity: 1; transform: translateY(0); letter-spacing: 0; }
  }
  @keyframes empty-dot-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.55; transform: scale(0.85); }
  }

  .empty-title {
    font-family: var(--font-prose);
    font-size: 32px;
    line-height: 1.15;
    letter-spacing: -0.02em;
    color: var(--fg-bright);
    font-weight: 400;
    max-width: 520px;
    animation: empty-line-in 0.7s cubic-bezier(0.22, 1, 0.36, 1) 0.1s both;
  }
  .empty-title-line {
    display: block;
  }
  .empty-title-accent {
    font-style: italic;
    color: var(--fg-dim);
    animation: empty-line-in 0.7s cubic-bezier(0.22, 1, 0.36, 1) 0.22s both;
  }
  @keyframes empty-line-in {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .empty-meta {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--muted);
    display: inline-flex;
    gap: 8px;
    animation: empty-line-in 0.7s cubic-bezier(0.22, 1, 0.36, 1) 0.34s both;
  }
  .empty-sep {
    color: var(--border2);
  }

  .empty-cta {
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 999px;
    color: var(--fg-dim);
    padding: 9px 22px 9px 14px;
    font-family: var(--font-mono);
    font-size: 12px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 10px;
    transition: border-color 0.18s ease, color 0.18s ease, background 0.18s ease;
    animation: empty-line-in 0.7s cubic-bezier(0.22, 1, 0.36, 1) 0.46s both;
  }
  .empty-cta:hover {
    border-color: var(--accent);
    color: var(--fg-bright);
    background: var(--accent-glow);
  }
  .empty-cta-kbd {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border: 1px solid var(--border2);
    border-radius: 6px;
    background: var(--surface2);
    font-size: 12px;
    color: var(--accent-soft);
    letter-spacing: 0;
  }

  .suggestions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: center;
    max-width: 480px;
    margin-top: 4px;
  }

  .suggestion-chip {
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 20px;
    color: var(--fg-dim);
    padding: 8px 18px;
    font-family: var(--font-ui);
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s, color 0.15s;
    white-space: nowrap;
  }
  .suggestion-chip:hover {
    background: var(--border);
    border-color: var(--accent-soft);
    color: var(--fg-bright);
  }

  /* Loading skeleton */
  .loading-skeleton {
    display: flex;
    flex-direction: column;
    gap: 24px;
    padding: 40px var(--chat-pad-x, 48px);
    max-width: 720px;
    margin: 0 auto;
    animation: skeleton-fade-in 0.3s ease;
  }
  @keyframes skeleton-fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }
  .skeleton-msg {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .skeleton-msg.skeleton-user {
    align-items: flex-end;
  }
  .skeleton-msg.skeleton-assistant {
    align-items: flex-start;
  }
  .skeleton-bar {
    height: 14px;
    border-radius: 6px;
    background: var(--surface2);
    animation: skeleton-pulse 1.4s ease-in-out infinite;
  }
  .skeleton-msg.skeleton-user .skeleton-bar {
    background: var(--border);
  }
  @keyframes skeleton-pulse {
    0%, 100% { opacity: 0.4; }
    50% { opacity: 0.8; }
  }

</style>
