<script>
  let { src, alt = 'output', mime = '' } = $props();
  let lightboxOpen = $state(false);

  let imageSrc = $derived(
    src.startsWith('data:') || src.startsWith('http') || src.startsWith('/')
      ? src
      : `data:${mime || 'image/png'};base64,${src}`
  );

  function openLightbox() {
    lightboxOpen = true;
  }

  function closeLightbox() {
    lightboxOpen = false;
  }

  function onKeydown(e) {
    if (e.key === 'Escape') closeLightbox();
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div class="image-container">
  <img
    src={imageSrc}
    {alt}
    class="rendered-image"
    onclick={openLightbox}
  />
</div>

{#if lightboxOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="lightbox-overlay" onclick={closeLightbox} onkeydown={onKeydown}>
    <img src={imageSrc} alt={alt} class="lightbox-image" />
    <button class="lightbox-close" onclick={closeLightbox}>&times;</button>
  </div>
{/if}

<style>
  .image-container {
    display: inline-block;
  }

  .rendered-image {
    max-width: 100%;
    max-height: 400px;
    border-radius: 4px;
    border: 1px solid var(--border2);
    cursor: zoom-in;
    transition: border-color 0.15s;
  }
  .rendered-image:hover { border-color: var(--accent); }

  .lightbox-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.85);
    z-index: 7000;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: zoom-out;
    animation: fade-in 0.15s ease;
  }
  @keyframes fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .lightbox-image {
    max-width: 90vw;
    max-height: 90vh;
    border-radius: 6px;
    box-shadow: 0 8px 40px rgba(0,0,0,0.5);
  }

  .lightbox-close {
    position: absolute;
    top: 16px;
    right: 20px;
    background: none;
    border: none;
    color: #fff;
    font-size: 28px;
    cursor: pointer;
    opacity: 0.7;
    transition: opacity 0.15s;
    line-height: 1;
  }
  .lightbox-close:hover { opacity: 1; }
</style>
