<script>
  import { currentConversation, appState } from '../state.svelte.js';
  import MessageBubble from './MessageBubble.svelte';

  let messagesEl = $state(null);
  let autoScroll = $state(true);

  // Track message count to trigger auto-scroll
  let messageCount = $derived(currentConversation()?.messages?.length || 0);
  let lastStreamContent = $derived(() => {
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
</script>

<div class="messages" bind:this={messagesEl} onscroll={onScroll}>
  {#if !currentConversation() || currentConversation().messages.length === 0}
    <div class="empty-state">
      <div class="empty-logo">
        <svg width="24" height="28" viewBox="0 0 10 12" fill="none"><path d="M6 0L0 7h4l-1 5 6-7H5l1-5z" fill="#fff"/></svg>
      </div>
      <h2>Start a conversation</h2>
      <p>An AI assistant with tools for code execution, data analysis, and visualization.</p>
    </div>
  {:else}
    {#each currentConversation().messages as msg (msg.id)}
      <MessageBubble {msg} />
    {/each}
  {/if}
</div>

{#if !autoScroll}
  <button class="scroll-bottom-btn" onclick={scrollToBottom}>scroll to bottom</button>
{/if}

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
    gap: 20px;
    padding: 40px;
    text-align: center;
  }

  .empty-logo {
    width: 48px; height: 48px;
    background: linear-gradient(135deg, var(--accent) 0%, #a78bfa 100%);
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 0 40px rgba(99,102,241,0.25);
    animation: float 4s ease-in-out infinite;
  }
  @keyframes float {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-5px); }
  }

  .empty-state h2 {
    font-size: 15px;
    color: var(--fg-dim);
    font-weight: 500;
    letter-spacing: 0.04em;
    font-family: 'Lora', serif;
  }
  .empty-state p {
    font-size: 11px;
    color: var(--muted);
    max-width: 340px;
    line-height: 1.9;
  }

  .scroll-bottom-btn {
    position: absolute;
    bottom: 80px;
    right: 36px;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 20px;
    color: var(--fg-dim);
    padding: 6px 14px;
    cursor: pointer;
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.05em;
    z-index: 50;
    transition: all 0.15s;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }
  .scroll-bottom-btn:hover { border-color: var(--accent); color: var(--fg); }
</style>
