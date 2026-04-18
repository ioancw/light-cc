// Keep --app-h in sync with the visible viewport.
//
// iOS Safari's `dvh` does not shrink when the on-screen keyboard opens,
// so an input fixed to the bottom ends up behind the keyboard. VisualViewport
// gives us the actual visible height; we publish it as a CSS custom property.

export function installViewportHeight() {
  if (typeof window === 'undefined') return;
  const root = document.documentElement;
  const vv = window.visualViewport;

  function apply() {
    const h = vv ? vv.height : window.innerHeight;
    root.style.setProperty('--app-h', `${h}px`);
  }

  apply();
  if (vv) {
    vv.addEventListener('resize', apply);
    vv.addEventListener('scroll', apply);
  } else {
    window.addEventListener('resize', apply);
  }
}
