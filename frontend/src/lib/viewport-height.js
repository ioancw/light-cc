// Keep --app-h in sync with the visible viewport, and work around iOS Safari
// tab-restore quirks (stale zoom, wrong visual viewport height).
//
// iOS Safari's `dvh` does not shrink when the on-screen keyboard opens,
// so an input fixed to the bottom ends up behind the keyboard. VisualViewport
// gives us the actual visible height; we publish it as a CSS custom property.
//
// Separately: when iOS suspends a tab (user switches apps or to another tab)
// and resumes it, the page may come back at a non-1.0 zoom — content overflows
// the viewport horizontally and the user has to pinch-zoom out. Toggling the
// viewport meta's maximum-scale forces Safari to re-apply the layout viewport.

const isIOS = typeof navigator !== 'undefined'
  && /iPad|iPhone|iPod/.test(navigator.userAgent)
  && !window.MSStream;

export function installViewportHeight() {
  if (typeof window === 'undefined') return;
  const root = document.documentElement;
  const vv = window.visualViewport;

  function apply() {
    const h = vv ? vv.height : window.innerHeight;
    root.style.setProperty('--app-h', `${h}px`);
  }

  // Force iOS to reset its saved zoom level to 1. We flip the viewport meta
  // briefly to maximum-scale=1, let the browser re-layout, then restore the
  // original content so pinch-zoom remains available.
  function resetIOSZoom() {
    if (!isIOS) return;
    const meta = document.querySelector('meta[name="viewport"]');
    if (!meta) return;
    const orig = meta.getAttribute('content');
    if (!orig || orig.includes('maximum-scale=1')) return;
    meta.setAttribute('content', `${orig}, maximum-scale=1`);
    requestAnimationFrame(() => {
      setTimeout(() => meta.setAttribute('content', orig), 300);
    });
  }

  apply();
  if (vv) {
    vv.addEventListener('resize', apply);
    vv.addEventListener('scroll', apply);
  } else {
    window.addEventListener('resize', apply);
  }

  // Restored from bfcache or reshown from another tab → reflow + reset zoom
  window.addEventListener('pageshow', (e) => {
    apply();
    if (e.persisted) resetIOSZoom();
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      apply();
      resetIOSZoom();
    }
  });
}
