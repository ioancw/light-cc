<script>
  import { onMount, onDestroy } from 'svelte';
  import { appState, getStreamingMessage, currentConversation, isCurrentStreaming, showToast, viewport } from '../state.svelte.js';
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
    const cap = viewport.isMobile ? 110 : 200;
    textareaEl.style.height = Math.min(textareaEl.scrollHeight, cap) + 'px';
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

  // iOS Safari tries to "scroll the focused field into view" by translating the
  // whole document upward, even when body has overflow:hidden. Undo it.
  function handleFocus() {
    if (!viewport.isMobile) return;
    const reset = () => {
      window.scrollTo(0, 0);
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
    };
    reset();
    requestAnimationFrame(reset);
    setTimeout(reset, 100);
    setTimeout(reset, 300);
  }

  function sendMessage() {
    if (isCurrentStreaming()) return;
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

    // Send with cid so the server routes to the correct conversation sub-session.
    // Always use conv.id (the local key) -- serverId is assigned after the first
    // turn and would create a new empty session on the server if used as cid.
    send('user_message', { text: trimmed }, conv.id);
  }

  function cancelGeneration() {
    const conv = currentConversation();
    send('cancel_generation', {}, conv?.id);
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
  async function uploadAll(files) {
    await Promise.all(Array.from(files).map(async (file) => {
      try {
        const data = await uploadFile('', file);
        text += (text ? '\n' : '') + data.path;
        autoResize();
        showToast(`Uploaded ${file.name}`, 'success');
      } catch (err) {
        showToast(`Upload failed: ${file.name}`, 'error');
      }
    }));
  }

  async function onDrop(e) {
    e.preventDefault();
    dragCounter = 0;
    dragOver = false;
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) return;
    await uploadAll(files);
  }

  // Attachment button
  function handleAttach() {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.onchange = async () => {
      if (!input.files || input.files.length === 0) return;
      await uploadAll(input.files);
    };
    input.click();
  }

  // Listen for text insertion from FilePanel
  function onInsertText(e) {
    text += (text ? '\n' : '') + e.detail;
    autoResize();
  }

  // Listen for suggestion chip clicks from ChatArea
  function onSuggestion(e) {
    text = e.detail.prompt;
    sendMessage();
  }

  onMount(() => {
    window.addEventListener('lcc-insert-text', onInsertText);
    window.addEventListener('lcc-suggestion', onSuggestion);
  });

  onDestroy(() => {
    window.removeEventListener('lcc-insert-text', onInsertText);
    window.removeEventListener('lcc-suggestion', onSuggestion);
  });
</script>

<div class="input-area">
  <div class="input-wrapper" class:drag-over={dragOver}
    ondragenter={onDragEnter} ondragover={onDragOver} ondragleave={onDragLeave} ondrop={onDrop} role="group">
    {#if acVisible && acMatches.length > 0}
      <div class="autocomplete-dropdown">
        {#each acMatches as skill, i (skill.name)}
          <button
            class="autocomplete-item"
            class:active={i === acIndex}
            onmousedown={() => selectAutocomplete(skill.name)}
            type="button"
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
          </button>
        {/each}
      </div>
    {/if}
    <textarea
      bind:this={textareaEl}
      bind:value={text}
      class="input-textarea"
      placeholder={viewport.isMobile ? 'Message...' : 'Send a message, or type / for commands...'}
      rows="1"
      oninput={handleInput}
      onkeydown={handleKeydown}
      onfocus={handleFocus}
    ></textarea>
    <div class="input-footer">
      <div class="input-hints">
        {#if isCurrentStreaming()}
          <div class="thinking-status">
            <div class="thinking-dot"></div>
            <span>{appState.inlineStatus ? appState.inlineStatus.message : 'Generating...'}</span>
          </div>
        {:else if appState.needsScrollDown}
          <button class="scroll-hint" onclick={() => appState.scrollToBottom?.()}>
            <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
              <path d="M6 2v8M2 7l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            scroll to bottom
          </button>
          <span><span class="kbd">Shift+Enter</span> newline</span>
        {:else}
          <span><span class="kbd">Enter</span> send</span>
          <span><span class="kbd">Shift+Enter</span> newline</span>
        {/if}
      </div>
      <div class="input-actions">
        <button class="attach-btn" onclick={handleAttach} title="Attach files">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M14 8l-5.5 5.5a3.5 3.5 0 01-5-5L9 3a2 2 0 013 3L6.5 11.5a.5.5 0 01-.7-.7L11 5.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        {#if isCurrentStreaming()}
          <button class="stop-btn" onclick={cancelGeneration} title="Stop generation">
            <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
              <rect x="2" y="2" width="8" height="8" rx="1" fill="currentColor"/>
            </svg>
          </button>
        {:else}
          <button class="send-btn" onclick={sendMessage} disabled={!text.trim() || !appState.connected} title="Send message">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1 11L11 6 1 1v4l7 1-7 1v4z" fill="currentColor"/>
            </svg>
          </button>
        {/if}
      </div>
    </div>
  </div>
</div>

<style>
  .input-area {
    padding: 12px 32px 20px;
    background: var(--bg);
    flex-shrink: 0;
    min-width: 0;
  }

  .input-wrapper {
    max-width: var(--content-max-w);
    width: 100%;
    margin: 0 auto;
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: 12px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
    position: relative;
  }
  .input-wrapper:focus-within {
    border-color: var(--muted);
    box-shadow: 0 1px 8px rgba(0,0,0,0.06);
  }
  .input-wrapper.drag-over {
    border-color: var(--accent);
    background: var(--accent-glow);
  }

  .input-textarea {
    width: 100%;
    background: transparent;
    border: none;
    padding: 14px 18px 10px;
    color: var(--fg-bright);
    font-family: var(--font-ui);
    font-size: 15px;
    line-height: 1.6;
    resize: none;
    min-height: 52px;
    max-height: 200px;
    overflow-y: auto;
  }
  .input-textarea::placeholder { color: var(--muted); }
  .input-textarea:focus { outline: none; }

  .input-footer {
    padding: 6px 16px 10px;
    display: flex; align-items: center; justify-content: space-between;
  }

  .input-hints {
    font-size: 12px;
    color: var(--muted);
    display: flex; gap: 12px;
    font-family: var(--font-ui);
  }

  .thinking-status {
    display: flex; align-items: center; gap: 8px;
    font-size: 12px;
    color: var(--dim);
    font-family: var(--font-ui);
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

  .input-actions {
    display: flex; align-items: center; gap: 6px;
  }

  .attach-btn {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: var(--muted);
    width: 32px; height: 32px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: color 0.15s ease;
    padding: 0;
  }
  .attach-btn:hover {
    color: var(--fg-dim);
  }

  .send-btn {
    background: var(--fg-bright);
    border: none;
    border-radius: 50%;
    color: var(--bg);
    width: 30px; height: 30px;
    cursor: pointer;
    transition: opacity 0.15s ease;
    display: flex; align-items: center; justify-content: center;
    padding: 0;
  }
  .send-btn:hover {
    opacity: 0.8;
  }
  .send-btn:active { opacity: 0.7; }
  .send-btn:disabled { opacity: 0.25; cursor: not-allowed; }

  .stop-btn {
    background: var(--red-soft);
    border: 1px solid var(--red);
    border-radius: 50%;
    color: var(--red);
    width: 30px; height: 30px;
    cursor: pointer;
    transition: all 0.15s ease;
    display: flex; align-items: center; justify-content: center;
    padding: 0;
  }
  .stop-btn:hover { background: var(--red); color: #fff; }
  .stop-btn:active { opacity: 0.8; }

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
    border: none;
    border-bottom: 1px solid var(--border);
    transition: background 0.1s;
    background: none;
    width: 100%;
    text-align: left;
    color: inherit;
    font: inherit;
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
    font-family: var(--font-mono);
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
    background: none;
    border: none;
    font: inherit;
    padding: 0;
  }
  .scroll-hint:hover {
    color: var(--accent-soft);
  }

  @media (max-width: 768px) {
    .input-area {
      padding: 8px 12px 12px;
      padding-bottom: calc(12px + env(safe-area-inset-bottom));
    }
    .input-textarea {
      padding: 10px 12px 8px;
      font-size: 16px; /* prevents iOS from zooming when the field is focused */
    }
    .input-hints span { display: none; }
    .input-hints .scroll-hint { display: flex; }
    .attach-btn, .send-btn, .stop-btn {
      width: 44px;
      height: 44px;
    }
    .autocomplete-item {
      padding: 12px 14px;
      min-height: 44px;
    }
    .autocomplete-name { font-size: 13px; }
    .autocomplete-desc { font-size: 12px; }
  }
</style>
