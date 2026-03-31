<script>
  import { onMount } from 'svelte';

  let { html, name = 'Embed' } = $props();
  let iframeEl = $state(null);

  onMount(() => {
    if (!iframeEl) return;
    // Auto-resize iframe to content height
    const resize = () => {
      try {
        const doc = iframeEl.contentDocument || iframeEl.contentWindow?.document;
        if (doc && doc.body) {
          iframeEl.style.height = Math.max(doc.body.scrollHeight + 20, 100) + 'px';
        }
      } catch {}
    };
    iframeEl.addEventListener('load', resize);
    return () => iframeEl.removeEventListener('load', resize);
  });
</script>

<div class="embed-container">
  <div class="embed-label">{name}</div>
  <iframe
    bind:this={iframeEl}
    class="embed-iframe"
    srcdoc={html}
    sandbox="allow-scripts"
    title={name}
  ></iframe>
</div>

<style>
  .embed-container {
    border: 1px solid var(--border2);
    border-radius: 6px;
    overflow: hidden;
    background: var(--surface);
  }

  .embed-label {
    padding: 6px 12px;
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
    font-family: 'Geist Mono', monospace;
  }

  .embed-iframe {
    width: 100%;
    min-height: 200px;
    border: none;
    background: #fff;
  }
</style>
