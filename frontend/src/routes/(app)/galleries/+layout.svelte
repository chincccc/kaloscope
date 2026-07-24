<script lang="ts">
  import { Drawer, Menu } from '$lib/components';
  import type { Snippet } from 'svelte';
  import type { LayoutData } from './$types';

  let { data, children }: { data: LayoutData; children: Snippet } = $props();
</script>

<Drawer>
  {@render children()}
  {#snippet side()}<Menu menus={data.menus} />{/snippet}
</Drawer>
