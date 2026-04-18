<script>
  import { renderMarkdown, highlightCode } from '../lib/markdown.js';
  import { formatTime, escapeHtml, modelLabel } from '../lib/utils.js';
  import { send } from '../ws.js';
  import { currentConversation } from '../state.svelte.js';
  import ToolCall from './ToolCall.svelte';
  import ToolGroup from './ToolGroup.svelte';
  import Chart from './renderers/Chart.svelte';
  import D3Chart from './renderers/D3Chart.svelte';
  import Image from './renderers/Image.svelte';
  import Table from './renderers/Table.svelte';

  let { msg } = $props();

  // Collapse runs of 3+ consecutive tool calls with the same name into a single
  // expandable group. Keeps the chat view scannable when e.g. 12 WebSearch
  // calls back-to-back would otherwise eat the viewport.
  const GROUP_THRESHOLD = 3;
  let groupedCalls = $derived.by(() => {
    const calls = msg.toolCalls || [];
    const out = [];
    let i = 0;
    while (i < calls.length) {
      let j = i + 1;
      while (j < calls.length && calls[j].name && calls[j].name === calls[i].name) j++;
      const run = j - i;
      if (run >= GROUP_THRESHOLD) {
        out.push({ kind: 'group', id: `grp_${calls[i].id}`, name: calls[i].name, calls: calls.slice(i, j) });
      } else {
        for (let k = i; k < j; k++) out.push({ kind: 'single', id: calls[k].id, tc: calls[k] });
      }
      i = j;
    }
    return out;
  });

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
      <svg width="14" height="14" viewBox="0 0 32 32" fill="none"><g transform="translate(16,16) scale(1.3) translate(-16,-16)"><path d="M24 4c-3 2-6 5-8 9s-3 8-3.5 11c-.1.8-.2 1.5-.2 2l-.3.5c-.5-.5-1.2-1.5-1.5-3-.4-1.8-.2-4 1-6.5 1.5-3 3-5.5 5-7.5s4-3.5 6-4.5c.5-.2.9-.4 1.2-.5L24 4z" fill="#fff" opacity=".5"/><path d="M24 4c-2 1-4 2.5-6 4.5s-3.5 4.5-5 7.5c-1.2 2.5-1.4 4.7-1 6.5.3 1.5 1 2.5 1.5 3l.3-.5c0-.5.1-1.2.2-2 .5-3 1.5-7 3.5-11s5-7 8-9l.2-.1-.5.1c-.3.1-.7.3-1.2.5z" fill="#fff"/><line x1="12.5" y1="25.5" x2="8" y2="28" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/></g></svg>
    </div>
  {/if}
  <div class="msg-body" bind:this={bodyEl}>
    <div class="msg-header">
      <span class="msg-role">
        {msg.role === 'user' ? 'You' : 'Wiggy'}
      </span>
      {#if msg.model && msg.role === 'assistant'}
        <span class="msg-model">{modelLabel(msg.model)}</span>
      {/if}
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
        {#each groupedCalls as item (item.id)}
          {#if item.kind === 'group'}
            <ToolGroup name={item.name} calls={item.calls} />
          {:else}
            <ToolCall tc={item.tc} />
          {/if}
        {/each}
      </div>

      <div class="inline-media">
        {#each msg.toolCalls as tc (tc.id)}
          {#if tc.d3Chart}
            <D3Chart spec={tc.d3Chart.spec} title={tc.d3Chart.title} />
          {/if}
          {#if tc.chart}
            <Chart plotlyJson={tc.chart.plotlyJson} title={tc.chart.title} />
          {/if}
          {#if tc.images && tc.images.length > 0}
            {#each tc.images as img}
              <Image src={img.data} alt={img.name || 'output'} mime={img.mime} />
            {/each}
          {/if}
          {#if tc.tables && tc.tables.length > 0}
            {#each tc.tables as html}
              <Table {html} />
            {/each}
          {/if}
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

  /* Role-colored left accent — the signature detail. Gives the chat column
     a rhythm that instantly distinguishes role without relying on bubbles. */
  .msg-row::before {
    content: '';
    position: absolute;
    left: 0;
    top: 24px;
    bottom: 24px;
    width: 2px;
    border-radius: 0 1px 1px 0;
    background: var(--border2);
  }
  .msg-row.user-row::before { background: var(--accent); }
  .msg-row.assistant-row::before { background: var(--accent-soft); opacity: 0.5; }

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

  .msg-model {
    font-size: 11px;
    color: var(--accent-soft);
    opacity: 0.7;
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

  .inline-media {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin: 8px 0;
  }
  .inline-media :global(img) {
    max-width: 100%;
    border-radius: 6px;
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
      padding: 16px 16px 16px 14px;
      grid-template-columns: 24px minmax(0, 1fr);
      gap: 10px;
    }
    .msg-row::before { top: 16px; bottom: 16px; }
    .msg-avatar { width: 24px; height: 24px; font-size: 10px; }
    .msg-actions { opacity: 1; }
    .msg-action-btn {
      min-height: 36px;
      min-width: 44px;
      padding: 8px 10px;
      font-size: 13px;
    }
  }
</style>
