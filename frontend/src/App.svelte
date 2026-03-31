<script>
  import { isAuthenticated } from './state.svelte.js';
  import { restoreTheme } from './theme.js';
  import Auth from './components/Auth.svelte';
  import Loom from './components/Loom.svelte';

  restoreTheme();
</script>

{#if isAuthenticated()}
  <Loom />
{:else}
  <Auth />
{/if}
