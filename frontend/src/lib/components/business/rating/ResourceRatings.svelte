<script lang="ts" module>
  export type RatingResourceType = 'media' | 'gallery_book';

  export type RatingDimensionValue = {
    key: string;
    name: string;
    removable: boolean;
    editable: boolean;
    score: number | null;
  };

  export type ResourceRatingsProps = {
    resourceType: RatingResourceType;
    resourceId: number;
    dark?: boolean;
    class?: string;
  };
</script>

<script lang="ts">
  import { api } from '$lib/api';
  import { _ } from '$lib/i18n';
  import type { Resp } from '$lib/types';
  import { onMount } from 'svelte';

  let { resourceType, resourceId, dark = false, class: className = '' }: ResourceRatingsProps = $props();
  let dimensions = $state<RatingDimensionValue[]>([]);
  let loading = $state(true);
  let saving = $state<string | null>(null);

  async function load() {
    loading = true;
    try {
      const response = await api
        .get(`rating/${resourceType}/${resourceId}`)
        .json<Resp<{ dimensions: RatingDimensionValue[] }>>();
      dimensions = response.data.dimensions;
    } finally {
      loading = false;
    }
  }

  async function save(dimension: RatingDimensionValue, score: number | null) {
    if (!dimension.editable || saving) return;
    const previous = dimension.score;
    dimension.score = score;
    saving = dimension.key;
    try {
      await api.post(`rating/${resourceType}/${resourceId}`, {
        json: { dimension_key: dimension.key, score }
      });
    } catch (error) {
      dimension.score = previous;
      throw error;
    } finally {
      saving = null;
    }
  }

  onMount(() => void load());
</script>

<section
  class="rounded-sm border p-3 {dark
    ? 'border-white/15 bg-black/70 text-white backdrop-blur-sm'
    : 'border-base-300 bg-base-100'} {className}"
  aria-label={$_('rating.title')}
>
  <div class="mb-2 flex min-h-6 items-center gap-2">
    <iconify-icon icon="fluent:star-24-filled" width="1rem" class="text-warning"></iconify-icon>
    <h2 class="text-sm font-semibold">{$_('rating.title')}</h2>
    {#if loading}<span class="loading ml-auto loading-xs loading-spinner"></span>{/if}
  </div>
  {#if !loading}
    <div class="space-y-3">
      {#each dimensions as dimension (dimension.key)}
        <div class="grid grid-cols-[minmax(4rem,auto)_1fr_auto] items-center gap-3">
          <span class="truncate text-sm" title={dimension.name}>{dimension.name}</span>
          {#if dimension.editable}
            <input
              type="range"
              class="range min-w-24 range-primary range-xs"
              min="1"
              max="10"
              step="1"
              value={dimension.score ?? 1}
              disabled={saving !== null}
              aria-label={dimension.name}
              onchange={(event) => void save(dimension, Number(event.currentTarget.value))}
            />
          {:else}
            <div class="h-1.5 min-w-24 overflow-hidden rounded-full bg-current/15">
              <div class="h-full bg-warning" style="width:{((dimension.score ?? 0) / 10) * 100}%"></div>
            </div>
          {/if}
          <div class="flex w-14 items-center justify-end gap-1 tabular-nums">
            <span class="text-sm font-semibold">{dimension.score ?? '-'}</span>
            <span class="text-xs opacity-50">/10</span>
            {#if dimension.editable && dimension.score !== null}
              <button
                class="btn btn-circle size-5 border-0 bg-transparent btn-ghost p-0 shadow-none"
                aria-label={$_('rating.clear')}
                title={$_('rating.clear')}
                disabled={saving !== null}
                onclick={() => void save(dimension, null)}
              >
                <iconify-icon icon="fluent:dismiss-16-regular" width="0.75rem"></iconify-icon>
              </button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</section>
