// Svelte action: fires `callback` when a click lands outside the node.
// Uses capture-phase so it runs before inner handlers that might stopPropagation.
//
// Usage:
//   <div use:clickOutside={() => open = false}>
//     ...
//   </div>

export function clickOutside(node, callback) {
  function onClick(e) {
    if (!node.contains(e.target)) callback(e);
  }
  document.addEventListener('click', onClick, true);
  return {
    update(next) { callback = next; },
    destroy() { document.removeEventListener('click', onClick, true); },
  };
}
