<script lang="ts">
  import { api } from '$lib/api';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { Resp } from '$lib/types';
  import { untrack } from 'svelte';

  type SearchHistory = {
    id: number;
    rel_id: number;
    keyword: string;
    updated_at: string;
  };

  let {
    relId,
    maxItems = 20,
    onselect
  }: {
    relId: number;
    maxItems?: number;
    onselect: (keyword: string) => void;
  } = $props();

  let items: SearchHistory[] = $state([]);
  let requestId = 0;

  export async function refresh() {
    const currentRequest = ++requestId;
    try {
      const response = await api
        .get('user/history/list', {
          searchParams: {
            rel_type: 'search',
            page_num: 0,
            ordering: '-updated_at'
          }
        })
        .json<Resp<{ items: SearchHistory[] }>>();
      if (currentRequest !== requestId) return;
      items = response.data.items.filter((item) => item.rel_id === relId).slice(0, maxItems);
    } catch {
      if (currentRequest === requestId) items = [];
    }
  }

  function remove(id: number) {
    api.post('user/history/delete', { json: { ids: [id] } }).then(() => {
      items = items.filter((item) => item.id !== id);
    });
  }

  $effect(() => {
    void relId;
    void maxItems;
    untrack(() => void refresh());
  });
</script>

{#if items.length}
  <div class="flex max-w-full flex-wrap justify-center gap-2">
    {#each items as item (item.id)}
      <span class="badge h-8 max-w-full gap-0 border-base-300 bg-base-200 px-1">
        <button
          type="button"
          class="max-w-48 truncate px-2 text-sm hover:text-primary"
          title={item.keyword}
          onclick={() => onselect(item.keyword)}
        >
          {item.keyword}
        </button>
        <button
          type="button"
          class="btn btn-circle size-6 border-0 bg-transparent btn-ghost shadow-none"
          title={$_('action.delete')}
          aria-label={$_('action.delete')}
          onclick={() => remove(item.id)}
        >
          <iconify-icon icon={icons.dismiss} width="0.875rem"></iconify-icon>
        </button>
      </span>
    {/each}
  </div>
{/if}
