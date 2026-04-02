<script>
  import { onMount, onDestroy } from 'svelte';
  import { appState, getStreamingMessage, currentConversation, showToast } from '../state.svelte.js';
  import { send } from '../ws.js';
  import { uploadFile } from '../api.js';

  let text = $state('');
  let textareaEl = $state(null);

  // Autocomplete state
  let acVisible = $state(false);
  let acMatches = $state([]);
  let acIndex = $state(0);

  // Drag-drop state
  let dragOver = $state(false);
  let dragCounter = 0;

  function autoResize() {
    if (!textareaEl) return;
    textareaEl.style.height = 'auto';
    textareaEl.style.height = Math.min(textareaEl.scrollHeight, 200) + 'px';
  }

  function updateAutocomplete() {
    const allSkills = appState.skills || [];
    // Deduplicate by name (skills + commands can overlap)
    const seen = new Set();
    const skills = allSkills.filter(s => { if (seen.has(s.name)) return false; seen.add(s.name); return true; });
    if (!textareaEl || skills.length === 0) { hideAutocomplete(); return; }

    const cursorPos = textareaEl.selectionStart;
    const textBefore = text.substring(0, cursorPos);

    if (!textBefore.startsWith('/') || textBefore.includes(' ')) {
      hideAutocomplete();
      return;
    }

    const query = textBefore.substring(1).toLowerCase();
    const matches = skills.filter(s => s.name.toLowerCase().startsWith(query));

    if (matches.length === 0) { hideAutocomplete(); return; }

    acMatches = matches;
    acIndex = 0;
    acVisible = true;
  }

  function hideAutocomplete() {
    acVisible = false;
    acMatches = [];
    acIndex = -1;
  }

  function selectAutocomplete(name) {
    text = '/' + name + ' ';
    hideAutocomplete();
    if (textareaEl) textareaEl.focus();
  }

  function handleKeydown(e) {
    // Autocomplete navigation
    if (acVisible && acMatches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        acIndex = (acIndex + 1) % acMatches.length;
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        acIndex = (acIndex - 1 + acMatches.length) % acMatches.length;
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        if (acMatches[acIndex]) selectAutocomplete(acMatches[acIndex].name);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        hideAutocomplete();
        return;
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function handleInput() {
    autoResize();
    updateAutocomplete();
  }

  function sendMessage() {
    if (appState.isStreaming) return;
    if (!appState.connected) return;

    const trimmed = text.trim();
    if (!trimmed) return;

    text = '';
    hideAutocomplete();
    if (textareaEl) textareaEl.style.height = 'auto';

    const conv = currentConversation();
    if (!conv) return;

    // Push user message
    conv.messages.push({
      role: 'user',
      content: trimmed,
      id: 'u_' + Date.now(),
      timestamp: Date.now(),
      toolCalls: [],
      streaming: false,
    });

    // Set title from first message
    if (conv.title === 'New conversation' && conv.messages.length === 1) {
      conv.title = trimmed.slice(0, 40) + (trimmed.length > 40 ? '...' : '');
    }

    // Add streaming placeholder for assistant
    conv.messages.push({
      role: 'assistant',
      content: '',
      id: 'msg_' + Date.now(),
      toolCalls: [],
      streaming: true,
      timestamp: Date.now(),
    });

    appState.isStreaming = true;
    send('user_message', { text: trimmed });
  }

  function cancelGeneration() {
    send('cancel_generation', {});
  }

  // Drag-and-drop
  function onDragEnter(e) {
    e.preventDefault();
    dragCounter++;
    dragOver = true;
  }
  function onDragOver(e) { e.preventDefault(); }
  function onDragLeave(e) {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) { dragCounter = 0; dragOver = false; }
  }
  async function onDrop(e) {
    e.preventDefault();
    dragCounter = 0;
    dragOver = false;
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) return;
    for (const file of files) {
      try {
        const data = await uploadFile('', file);
        text += (text ? '\n' : '') + data.path;
        autoResize();
        showToast(`Uploaded ${file.name}`, 'success');
      } catch (err) {
        showToast(`Upload failed: ${file.name}`, 'error');
      }
    }
  }

  // Listen for text insertion from FilePanel
  function onInsertText(e) {
    text += (text ? '\n' : '') + e.detail;
    autoResize();
  }

  onMount(() => {
    window.addEventListener('lcc-insert-text', onInsertText);
  });

  onDestroy(() => {
    window.removeEventListener('lcc-insert-text', onInsertText);
  });
</script>

<div class="input-area">
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="input-wrapper" class:drag-over={dragOver}
    ondragenter={onDragEnter} ondragover={onDragOver} ondragleave={onDragLeave} ondrop={onDrop}>
    {#if acVisible && acMatches.length > 0}
      <div class="autocomplete-dropdown">
        {#each acMatches as skill, i (i)}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div
            class="autocomplete-item"
            class:active={i === acIndex}
            onmousedown={() => selectAutocomplete(skill.name)}
          >
            <div class="autocomplete-top">
              <span class="autocomplete-name">/{skill.name}</span>
              {#if skill.argument_hint}
                <span class="autocomplete-hint">{skill.argument_hint}</span>
              {/if}
            </div>
            {#if skill.description}
              <div class="autocomplete-desc">{skill.description}</div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
    <textarea
      bind:this={textareaEl}
      bind:value={text}
      class="input-textarea"
      placeholder="Send a message..."
      rows="1"
      oninput={handleInput}
      onkeydown={handleKeydown}
    ></textarea>
    <div class="input-footer">
      <div class="input-hints">
        {#if appState.isStreaming}
          <div class="thinking-status">
            <div class="thinking-dot"></div>
            <span>{appState.inlineStatus ? appState.inlineStatus.message : 'Generating...'}</span>
          </div>
        {:else if appState.needsScrollDown}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <span class="scroll-hint" onclick={() => appState.scrollToBottom?.()}>
            <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
              <path d="M6 2v8M2 7l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            scroll to bottom
          </span>
          <span><span class="kbd">Shift+Enter</span> newline</span>
        {:else}
          <span><span class="kbd">Enter</span> send</span>
          <span><span class="kbd">Shift+Enter</span> newline</span>
        {/if}
      </div>
      {#if appState.isStreaming}
        <button class="stop-btn" onclick={cancelGeneration}>
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
            <rect x="2" y="2" width="8" height="8" rx="1" fill="currentColor"/>
          </svg>
          Stop
        </button>
      {:else}
        <button class="send-btn" onclick={sendMessage} disabled={!text.trim() || !appState.connected}>
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
            <path d="M1 11L11 6 1 1v4l7 1-7 1v4z" fill="currentColor"/>
          </svg>
          Send
        </button>
      {/if}
    </div>
  </div>
</div>

<style>
  .input-area {
    padding: 16px 28px 22px;
    border-top: 1px solid var(--border);
    background: linear-gradient(to top, var(--bg) 60%, color-mix(in srgb, var(--bg) 90%, transparent));
    flex-shrink: 0;
  }

  .input-wrapper {
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: 8px;
    transition: border-color 0.2s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  }
  .input-wrapper:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow), 0 4px 20px rgba(0,0,0,0.12);
  }
  .input-wrapper.drag-over {
    border-color: var(--accent);
    background: var(--accent-glow);
    box-shadow: 0 0 0 3px var(--accent-glow);
  }

  .input-textarea {
    width: 100%;
    background: transparent;
    border: none;
    padding: 14px 16px 10px;
    color: var(--fg-bright);
    font-family: 'Geist Mono', monospace;
    font-size: 13px;
    line-height: 1.6;
    resize: none;
    min-height: 52px;
    max-height: 200px;
    overflow-y: auto;
    letter-spacing: 0.02em;
  }
  .input-textarea::placeholder { color: var(--muted); }
  .input-textarea:focus { outline: none; }

  .input-footer {
    padding: 8px 14px 10px;
    display: flex; align-items: center; justify-content: space-between;
  }

  .input-hints {
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.08em;
    display: flex; gap: 12px;
  }

  .thinking-status {
    display: flex; align-items: center; gap: 8px;
    font-size: 11px;
    color: var(--dim);
    letter-spacing: 0.05em;
  }
  .thinking-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--accent-soft);
    animation: thinking-pulse 1.2s ease-in-out infinite;
  }
  @keyframes thinking-pulse {
    0%, 100% { opacity: 0.3; }
    50% { opacity: 1; }
  }

  .send-btn {
    background: var(--accent);
    border: none;
    border-radius: 5px;
    color: #fff;
    padding: 7px 18px;
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.06em;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    display: flex; align-items: center; gap: 6px;
  }
  .send-btn:hover {
    background: #4f46e5;
    box-shadow: 0 0 20px rgba(99,102,241,0.4);
    transform: translateY(-1px);
  }
  .send-btn:active { transform: translateY(0); }
  .send-btn:disabled { opacity: 0.4; cursor: not-allowed; box-shadow: none; transform: none; }

  .stop-btn {
    background: var(--red-soft);
    border: 1px solid var(--red);
    border-radius: 5px;
    color: var(--red);
    padding: 7px 18px;
    font-family: 'Geist Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.06em;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    display: flex; align-items: center; gap: 6px;
  }
  .stop-btn:hover { background: var(--red); color: #fff; transform: translateY(-1px); }
  .stop-btn:active { transform: translateY(0); }

  /* Autocomplete */
  .autocomplete-dropdown {
    position: absolute;
    bottom: 100%;
    left: 0; right: 0;
    max-height: 240px;
    overflow-y: auto;
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    margin-bottom: 4px;
    box-shadow: 0 -4px 16px rgba(0,0,0,0.3);
    z-index: 100;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }

  .autocomplete-item {
    padding: 8px 14px;
    cursor: pointer;
    border-bottom: 1px solid var(--border);
    transition: background 0.1s;
  }
  .autocomplete-item:last-child { border-bottom: none; }
  .autocomplete-item:hover, .autocomplete-item.active {
    background: var(--surface2);
  }

  .autocomplete-top {
    display: flex; align-items: baseline; gap: 8px;
  }

  .autocomplete-name {
    font-size: 12px;
    font-weight: 600;
    color: var(--accent-soft);
    font-family: 'Geist Mono', monospace;
  }

  .autocomplete-hint {
    font-size: 11px;
    color: var(--muted);
    font-style: italic;
  }

  .autocomplete-desc {
    font-size: 11px;
    color: var(--dim);
    margin-top: 2px;
    line-height: 1.4;
  }

  .scroll-hint {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 5px;
    color: var(--fg-dim);
    transition: color 0.15s ease;
    font-size: 11px;
    letter-spacing: 0.05em;
  }
  .scroll-hint:hover {
    color: var(--accent-soft);
  }

  @media (max-width: 768px) {
    .input-area { padding: 8px 12px 12px; }
    .input-textarea { padding: 10px 12px 8px; font-size: 14px; }
    .input-hints span:not(:first-child) { display: none; }
  }
</style>
