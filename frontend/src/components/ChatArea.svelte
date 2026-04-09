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
        <svg width="28" height="28" viewBox="0 0 32 32" fill="none"><g transform="translate(16,16) scale(1.3) translate(-16,-16)"><path d="M24 4c-3 2-6 5-8 9s-3 8-3.5 11c-.1.8-.2 1.5-.2 2l-.3.5c-.5-.5-1.2-1.5-1.5-3-.4-1.8-.2-4 1-6.5 1.5-3 3-5.5 5-7.5s4-3.5 6-4.5c.5-.2.9-.4 1.2-.5L24 4z" fill="#fff" opacity=".5"/><path d="M24 4c-2 1-4 2.5-6 4.5s-3.5 4.5-5 7.5c-1.2 2.5-1.4 4.7-1 6.5.3 1.5 1 2.5 1.5 3l.3-.5c0-.5.1-1.2.2-2 .5-3 1.5-7 3.5-11s5-7 8-9l.2-.1-.5.1c-.3.1-.7.3-1.2.5z" fill="#fff"/><line x1="12.5" y1="25.5" x2="8" y2="28" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/></g></svg>
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
    gap: 16px;
    padding: 40px;
    text-align: center;
    animation: empty-fade-in 0.4s ease;
  }
  @keyframes empty-fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .empty-logo {
    width: 40px; height: 40px;
    background: var(--fg-bright);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
  }

  .empty-state h2 {
    font-size: 20px;
    color: var(--fg-bright);
    font-weight: 600;
    letter-spacing: -0.02em;
    font-family: var(--font-prose);
  }
  .empty-state p {
    font-size: 14px;
    color: var(--muted);
    max-width: 340px;
    line-height: 1.6;
    font-family: var(--font-ui);
  }
  .empty-cta {
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 8px;
    color: var(--fg-dim);
    padding: 10px 24px;
    font-family: var(--font-ui);
    font-size: 14px;
    cursor: pointer;
    transition: background 0.15s ease;
  }
  .empty-cta:hover {
    background: var(--border);
    color: var(--fg-bright);
  }

</style>
