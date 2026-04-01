<script>
  import { renderMarkdown, highlightCode } from '../lib/markdown.js';
  import { formatTime, escapeHtml } from '../lib/utils.js';
  import { send } from '../ws.js';
  import ToolCall from './ToolCall.svelte';

  let { msg } = $props();

  let bodyEl = $state(null);

  $effect(() => {
    if (bodyEl && msg.role === 'assistant' && msg.content) {
      requestAnimationFrame(() => highlightCode(bodyEl));
    }
  });

  function copyMessage() {
    if (msg.content) {
      navigator.clipboard.writeText(msg.content);
    }
  }

  function retryMessage() {
    send('retry', {});
  }

  function forkConversation() {
    send('fork_conversation', {});
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

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="msg-row" class:user-row={msg.role === 'user'} class:assistant-row={msg.role === 'assistant'} onclick={handleCopyClick}>
  {#if msg.role === 'user'}
    <div class="msg-avatar user-av">U</div>
  {:else}
    <div class="msg-avatar ai-av">
      <svg width="12" height="14" viewBox="0 0 10 12" fill="none"><path d="M6 0L0 7h4l-1 5 6-7H5l1-5z" fill="#fff"/></svg>
    </div>
  {/if}
  <div class="msg-body" bind:this={bodyEl}>
    <div class="msg-header">
      <span class="msg-role" class:user={msg.role === 'user'} class:assistant={msg.role === 'assistant'}>
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
    padding: 10px 28px;
    border-bottom: 1px solid var(--border);
    display: grid;
    grid-template-columns: 28px 1fr;
    gap: 14px;
    align-items: start;
    animation: msg-in 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    transition: background 0.15s ease;
  }
  @keyframes msg-in {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .msg-row.user-row { background: var(--surface); }
  .msg-row.assistant-row { background: var(--bg); }
  .msg-row.assistant-row::before {
    content: '';
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 2px;
    background: linear-gradient(to bottom, var(--accent) 0%, transparent 100%);
    opacity: 0.5;
  }
  .msg-row:hover { background: color-mix(in srgb, var(--surface2) 30%, transparent); }

  .msg-avatar {
    width: 28px; height: 28px;
    border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 600;
    flex-shrink: 0;
    margin-top: 1px;
    transition: transform 0.2s ease;
  }
  .msg-row:hover .msg-avatar { transform: scale(1.05); }
  .msg-avatar.user-av {
    background: var(--surface2);
    border: 1px solid var(--border2);
    color: var(--fg-dim);
  }
  .msg-avatar.ai-av {
    background: linear-gradient(135deg, #4338ca 0%, #7c3aed 100%);
    color: #fff;
    box-shadow: 0 0 14px rgba(124,58,237,0.3);
  }

  .msg-body { min-width: 0; max-width: 100%; overflow: hidden; }

  .msg-header {
    display: flex; align-items: baseline; gap: 10px;
    margin-bottom: 4px;
  }
  .msg-role {
    font-size: 11px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    font-weight: 600;
  }
  .msg-role.user { color: var(--fg-dim); }
  .msg-role.assistant { color: var(--accent-soft); }

  .msg-time {
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.05em;
  }

  .msg-actions {
    margin-left: auto;
    display: flex; gap: 4px;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .msg-row:hover .msg-actions { opacity: 1; }
  .assistant-row .msg-actions { opacity: 1; }

  .msg-action-btn {
    background: transparent;
    border: 1px solid transparent;
    color: var(--muted);
    padding: 3px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    font-family: 'Geist Mono', monospace;
    letter-spacing: 0.05em;
    transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
  }
  .msg-action-btn:hover { border-color: var(--border2); color: var(--fg-dim); background: var(--surface2); }

  .tool-calls-container {
    margin: 10px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  /* Prose is styled globally in global.css */
  .msg-prose :global(a) { color: var(--accent-soft); text-decoration: underline; }

  .streaming-indicator {
    display: flex; align-items: center; gap: 10px;
    padding: 4px 0;
    color: var(--dim);
    font-size: 11px;
  }
  .stream-dots {
    display: flex; gap: 4px;
  }
  .stream-dots span {
    width: 5px; height: 5px;
    border-radius: 50%;
    background: var(--accent);
    animation: dot-bounce 1.2s ease-in-out infinite;
  }
  .stream-dots span:nth-child(2) { animation-delay: 0.15s; }
  .stream-dots span:nth-child(3) { animation-delay: 0.3s; }
  @keyframes dot-bounce {
    0%, 100% { opacity: 0.2; transform: translateY(0); }
    50% { opacity: 1; transform: translateY(-3px); }
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
</style>
