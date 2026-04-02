<script>
  import { currentConversation, appState } from '../state.svelte.js';
  import MessageBubble from './MessageBubble.svelte';

  let messagesEl = $state(null);
  let autoScroll = $state(true);

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
  {#if !currentConversation() || currentConversation().messages.length === 0}
    <div class="empty-state">
      <div class="empty-logo">
        <svg width="24" height="28" viewBox="0 0 10 12" fill="none"><path d="M6 0L0 7h4l-1 5 6-7H5l1-5z" fill="#fff"/></svg>
      </div>
      <h2>Start a conversation</h2>
      <p>An AI assistant with tools for code execution, data analysis, and visualization.</p>
      <button class="empty-cta" onclick={() => document.querySelector('.input-textarea')?.focus()}>
        Type a message to begin
      </button>
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
    gap: 24px;
    padding: 40px;
    text-align: center;
    animation: empty-fade-in 0.5s ease;
  }
  @keyframes empty-fade-in {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .empty-logo {
    width: 52px; height: 52px;
    background: linear-gradient(135deg, var(--accent) 0%, #a78bfa 100%);
    border-radius: 14px;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 0 40px rgba(99,102,241,0.25), 0 8px 32px rgba(99,102,241,0.15);
    animation: float 4s ease-in-out infinite;
    position: relative;
  }
  .empty-logo::after {
    content: '';
    position: absolute;
    inset: -4px;
    border-radius: 18px;
    background: linear-gradient(135deg, var(--accent) 0%, #a78bfa 100%);
    opacity: 0.15;
    z-index: -1;
    animation: float 4s ease-in-out infinite reverse;
  }
  @keyframes float {
    0%, 100% { transform: translateY(0) rotate(0deg); }
    50% { transform: translateY(-6px) rotate(1deg); }
  }

  .empty-state h2 {
    font-size: 17px;
    color: var(--fg-dim);
    font-weight: 500;
    letter-spacing: -0.01em;
    font-family: 'Lora', serif;
  }
  .empty-state p {
    font-size: 12px;
    color: var(--muted);
    max-width: 360px;
    line-height: 1.8;
  }
  .empty-cta {
    background: transparent;
    border: 1px dashed var(--border2);
    border-radius: 8px;
    color: var(--fg-dim);
    padding: 10px 24px;
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.04em;
    cursor: pointer;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  }
  .empty-cta:hover {
    border-color: var(--accent);
    border-style: solid;
    color: var(--accent-soft);
    background: var(--accent-glow);
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(99,102,241,0.2);
  }

</style>
