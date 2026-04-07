<script>
  import { renderMarkdown, highlightCode } from '../lib/markdown.js';
  import { formatTime, escapeHtml } from '../lib/utils.js';
  import { send } from '../ws.js';
  import { currentConversation } from '../state.svelte.js';
  import ToolCall from './ToolCall.svelte';

  let { msg } = $props();

  let bodyEl = $state(null);
  let highlightTimer = null;

  $effect(() => {
    if (bodyEl && msg.role === 'assistant' && msg.content) {
      if (msg.streaming) {
        // Debounce during streaming -- highlight at most every 500ms
        if (!highlightTimer) {
          highlightTimer = setTimeout(() => {
            highlightTimer = null;
            requestAnimationFrame(() => highlightCode(bodyEl));
          }, 500);
        }
      } else {
        // Immediate highlight when streaming is done
        clearTimeout(highlightTimer);
        highlightTimer = null;
        requestAnimationFrame(() => highlightCode(bodyEl));
      }
    }
  });

  function copyMessage() {
    if (msg.content) {
      navigator.clipboard.writeText(msg.content);
    }
  }

  function retryMessage() {
    const conv = currentConversation();
    send('retry', {}, conv?.id);
  }

  function forkConversation() {
    const conv = currentConversation();
    send('fork_conversation', {}, conv?.id);
  }

  function handleCopyClick(e) {
    const btn = e.target.closest('.copy-btn[data-code]');
    if (!btn) return;
    navigator.clipboard.writeText(btn.dataset.code).then(() => {
      btn.textContent = 'copied!';
      btn.classList.add('copied');
      setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('copied'); }, 1500);
    });
  }
</script>

<div class="msg-row" class:user-row={msg.role === 'user'} class:assistant-row={msg.role === 'assistant'} onclick={handleCopyClick} role="article">
  {#if msg.role === 'user'}
    <div class="msg-avatar user-av">U</div>
  {:else}
    <div class="msg-avatar ai-av">
      <svg width="12" height="14" viewBox="0 0 10 12" fill="none"><path d="M6 0L0 7h4l-1 5 6-7H5l1-5z" fill="#fff"/></svg>
    </div>
  {/if}
  <div class="msg-body" bind:this={bodyEl}>
    <div class="msg-header">
      <span class="msg-role">
        {msg.role === 'user' ? 'You' : 'Light CC'}
      </span>
      {#if msg.timestamp}
        <span class="msg-time">{formatTime(msg.timestamp)}</span>
      {/if}
      <div class="msg-actions">
        {#if msg.content}
          <button class="msg-action-btn" onclick={copyMessage}>copy</button>
        {/if}
        {#if msg.role === 'assistant' && !msg.streaming}
          <button class="msg-action-btn" onclick={retryMessage}>retry</button>
          <button class="msg-action-btn" onclick={forkConversation}>fork</button>
        {/if}
      </div>
    </div>

    {#if msg.toolCalls && msg.toolCalls.length > 0}
      <div class="tool-calls-container">
        {#each msg.toolCalls as tc (tc.id)}
          <ToolCall {tc} />
        {/each}
      </div>
    {/if}

    <div class="msg-prose">
      {#if msg.role === 'user'}
        {@html escapeHtml(msg.content).replace(/\n/g, '<br>')}
      {:else if msg.content}
        {@html renderMarkdown(msg.content)}
        {#if msg.streaming}
          <span class="cursor-blink"></span>
        {/if}
      {:else if msg.streaming}
        <div class="streaming-indicator">
          <div class="stream-dots"><span></span><span></span><span></span></div>
          <span>Generating...</span>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .msg-row {
    padding: 24px max(32px, calc((100% - var(--content-max-w)) / 2));
    display: grid;
    grid-template-columns: 26px minmax(0, 1fr);
    gap: 14px;
    align-items: start;
    animation: msg-in 0.3s ease;
    position: relative;
  }
  @keyframes msg-in {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .msg-row.user-row { background: transparent; }
  .msg-row.assistant-row { background: transparent; }

  .msg-avatar {
    width: 26px; height: 26px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 600;
    flex-shrink: 0;
    margin-top: 2px;
  }
  .msg-avatar.user-av {
    background: var(--surface2);
    border: 1px solid var(--border2);
    color: var(--fg-dim);
  }
  .msg-avatar.ai-av {
    background: var(--accent);
    color: #fff;
  }

  .msg-body { min-width: 0; max-width: 100%; overflow: hidden; }

  .msg-header {
    display: flex; align-items: baseline; gap: 10px;
    margin-bottom: 6px;
  }
  .msg-role {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.01em;
    font-family: var(--font-ui);
  }

  .msg-time {
    font-size: 12px;
    color: var(--muted);
  }

  .msg-actions {
    margin-left: auto;
    display: flex; gap: 2px;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .msg-row:hover .msg-actions { opacity: 1; }

  .msg-action-btn {
    background: transparent;
    border: none;
    color: var(--muted);
    padding: 3px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    font-family: var(--font-ui);
    transition: color 0.15s, background 0.15s;
  }
  .msg-action-btn:hover { color: var(--fg-dim); background: var(--surface2); }

  .tool-calls-container {
    margin: 8px 0;
    padding: 4px 0;
    display: flex;
    flex-direction: column;
    gap: 0;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 2px 8px;
  }

  /* Prose is styled globally in global.css */
  .msg-prose :global(a) { color: var(--accent-soft); text-decoration: underline; }

  .streaming-indicator {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 0;
    color: var(--dim);
    font-size: 13px;
    font-family: var(--font-ui);
  }
  .stream-dots {
    display: flex; gap: 4px;
  }
  .stream-dots span {
    width: 4px; height: 4px;
    border-radius: 50%;
    background: var(--accent);
    animation: dot-bounce 1.4s ease-in-out infinite;
  }
  .stream-dots span:nth-child(2) { animation-delay: 0.15s; }
  .stream-dots span:nth-child(3) { animation-delay: 0.3s; }
  @keyframes dot-bounce {
    0%, 100% { opacity: 0.2; transform: translateY(0); }
    50% { opacity: 1; transform: translateY(-2px); }
  }

  .cursor-blink {
    display: inline-block;
    width: 2px; height: 1.1em;
    background: var(--accent-soft);
    vertical-align: text-bottom;
    margin-left: 1px;
    animation: cursor-blink-anim 0.75s step-end infinite;
  }
  @keyframes cursor-blink-anim {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }

  @media (max-width: 768px) {
    .msg-row {
      padding: 16px 16px;
      grid-template-columns: 22px minmax(0, 1fr);
      gap: 10px;
    }
    .msg-avatar { width: 22px; height: 22px; font-size: 10px; }
  }
</style>
