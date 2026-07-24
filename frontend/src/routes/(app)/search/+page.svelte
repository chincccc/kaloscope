<script lang="ts">
  import { goto, replaceState } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '$lib/api';
  import { Container, Image, RatingBadges, SearchHistoryChips } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { RatingDimension, RatingValue, Resp } from '$lib/types';
  import { onMount } from 'svelte';

  type MediaResult = {
    id: number;
    lib_id: number;
    lib_name: string;
    lib_type: string;
    name: string;
    poster: string | null;
    year: number | null;
    parent_id?: number;
    parent_name?: string;
    season?: number;
    episode?: number;
    tags?: string[];
    ratings?: RatingValue[];
  };

  type GalleryResult = {
    id: number;
    gallery_id: number;
    gallery_name: string;
    book_name: string | null;
    name: string | null;
    item_count?: number;
    uncategorized?: boolean;
    tags?: string[];
    ratings?: RatingValue[];
  };

  type Results = {
    movies: MediaResult[];
    tv_shows: MediaResult[];
    episodes: MediaResult[];
    books: GalleryResult[];
    images: GalleryResult[];
    totals: Record<'movies' | 'tv_shows' | 'episodes' | 'books' | 'images', number>;
  };

  type RatingFilter = { id: number; dimension: string; minimum: number };
  type SearchType = 'movie' | 'tv_show' | 'gallery_book' | 'episode' | 'image';
  const searchTypes: Array<{ value: SearchType; label: string }> = [
    { value: 'movie', label: 'media.local_search.types.movie' },
    { value: 'tv_show', label: 'media.local_search.types.tv_show' },
    { value: 'gallery_book', label: 'media.local_search.types.gallery_book' },
    { value: 'episode', label: 'media.local_search.types.episode' },
    { value: 'image', label: 'media.local_search.types.image' }
  ];

  const loading = createLoading();
  let selectedTypes = $state<SearchType[]>(searchTypes.map((item) => item.value));
  let ratingDimensions = $state<RatingDimension[]>([]);
  let ratingFilters = $state<RatingFilter[]>([]);
  let nextRatingFilterId = 1;
  let searchValue = $state('');
  let activeKeyword = $state('');
  let history: SearchHistoryChips | null = $state(null);
  let searched = $state(false);
  let results: Results | null = $state(null);
  let controller: AbortController | null = null;

  function toggleType(value: SearchType) {
    if (selectedTypes.includes(value)) {
      if (selectedTypes.length > 1) selectedTypes = selectedTypes.filter((item) => item !== value);
    } else {
      selectedTypes = [...selectedTypes, value];
    }
    rerunSearch();
  }

  function rerunSearch() {
    if (searched && activeKeyword) {
      void search(activeKeyword, { recordHistory: false });
    }
  }

  function addRatingFilter() {
    const used = new Set(ratingFilters.map((filter) => filter.dimension));
    const dimension = ratingDimensions.find((item) => !used.has(item.key));
    if (!dimension) return;
    ratingFilters.push({ id: nextRatingFilterId++, dimension: dimension.key, minimum: 1 });
    rerunSearch();
  }

  function removeRatingFilter(id: number) {
    ratingFilters = ratingFilters.filter((filter) => filter.id !== id);
    rerunSearch();
  }

  function availableRatingDimensions(filter: RatingFilter) {
    const used = new Set(ratingFilters.filter((item) => item.id !== filter.id).map((item) => item.dimension));
    return ratingDimensions.filter((dimension) => !used.has(dimension.key));
  }

  function authenticatedImage(node: HTMLImageElement, initialItemId: number) {
    let objectUrl: string | null = null;
    let abortController: AbortController | null = null;
    function load(itemId: number) {
      abortController?.abort();
      abortController = new AbortController();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      api
        .get(`gallery/cover/${itemId}`, { signal: abortController.signal })
        .blob()
        .then((blob) => {
          objectUrl = URL.createObjectURL(blob);
          node.src = objectUrl;
        })
        .catch(() => {});
    }
    load(initialItemId);
    return {
      update: load,
      destroy() {
        abortController?.abort();
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      }
    };
  }

  function authenticatedMediaImage(node: HTMLImageElement, initialItemId: number) {
    let objectUrl: string | null = null;
    let abortController: AbortController | null = null;
    function load(itemId: number) {
      abortController?.abort();
      abortController = new AbortController();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      api
        .get('media/cover/' + itemId, { signal: abortController.signal, cache: 'no-store' })
        .blob()
        .then((blob) => {
          objectUrl = URL.createObjectURL(blob);
          node.src = objectUrl;
        })
        .catch(() => {});
    }
    load(initialItemId);
    return {
      update: load,
      destroy() {
        abortController?.abort();
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      }
    };
  }

  async function search(keyword: string = searchValue, options: { recordHistory?: boolean } = {}) {
    const { recordHistory = true } = options;
    const value = keyword.trim();
    if (!value) {
      results = null;
      searched = false;
      return;
    }
    activeKeyword = value;
    searchValue = value;
    controller?.abort();
    const requestController = new AbortController();
    controller = requestController;
    loading.start();
    try {
      const response = await api
        .get('search', {
          signal: requestController.signal,
          searchParams: {
            keyword: value,
            page_num: 0,
            types: selectedTypes.join(','),
            ...(ratingFilters.length
              ? { rating_filters: ratingFilters.map((item) => `${item.dimension}:${item.minimum}`).join(',') }
              : {})
          }
        })
        .json<Resp<Results>>();
      if (controller !== requestController || requestController.signal.aborted) return;
      results = response.data;
      searched = true;
      const url = new URL(page.url);
      if (recordHistory) {
        api
          .post('user/history/record', {
            json: { rel_type: 'search', rel_id: 0, keyword: value }
          })
          .then(() => history?.refresh());
      }
      url.searchParams.delete('keyword');
      replaceState(url, page.state);
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        results = null;
        searched = true;
      }
    } finally {
      if (controller === requestController) {
        controller = null;
        loading.end();
      }
    }
  }

  function galleryTitle(item: GalleryResult) {
    return item.uncategorized ? $_('media.local_search.uncategorized') : item.name || '';
  }

  function mediaPath(item: MediaResult) {
    const episode = item.parent_id ? `?episode_id=${item.id}` : '';
    return `/medialibs/${item.lib_id}/${item.parent_id ?? item.id}${episode}`;
  }

  onMount(() => {
    api
      .get('rating/dimensions')
      .json<Resp<RatingDimension[]>>()
      .then((response) => (ratingDimensions = response.data));
    if (page.url.pathname === '/search') {
      void goto(`/websearch/local${page.url.search}`, { replaceState: true });
      return;
    }

    const params = page.url.searchParams;
    const restoredKeyword = params.get('restore') === 'false' ? params.get('keyword') || '' : '';
    const url = new URL(page.url);
    url.searchParams.delete('keyword');
    url.searchParams.delete('restore');
    replaceState(url, page.state);
    if (restoredKeyword) void search(restoredKeyword);
    return () => controller?.abort();
  });
</script>

<Container class="pull-to-refresh">
  <div class="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
    <div class="mx-auto flex max-w-2xl flex-col gap-3">
      <form
        class="flex gap-2"
        onsubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <label class="input min-w-0 flex-1">
          <iconify-icon icon={icons.search} width="1.25rem" class="opacity-60"></iconify-icon>
          <input bind:value={searchValue} placeholder={$_('media.local_search.placeholder')} autocomplete="off" />
        </label>
        <button class="btn btn-square btn-primary" type="submit" aria-label={$_('nav.local_search.title')}>
          <iconify-icon icon={icons.search} width="1.25rem"></iconify-icon>
        </button>
      </form>
      <fieldset class="flex flex-wrap gap-2" aria-label={$_('media.local_search.filter_types')}>
        {#each searchTypes as type (type.value)}
          <label
            class="badge h-7 cursor-pointer gap-1.5 border transition-colors"
            class:badge-primary={selectedTypes.includes(type.value)}
            class:badge-outline={!selectedTypes.includes(type.value)}
          >
            <input
              type="checkbox"
              class="checkbox checkbox-xs"
              checked={selectedTypes.includes(type.value)}
              onchange={() => toggleType(type.value)}
            />
            {$_(type.label)}
          </label>
        {/each}
      </fieldset>
      <fieldset class="flex flex-col gap-2" aria-label={$_('media.local_search.filter_rating')}>
        {#each ratingFilters as filter (filter.id)}
          <div class="flex flex-wrap items-center gap-2">
            <select
              class="select min-w-36 select-sm"
              bind:value={filter.dimension}
              onchange={rerunSearch}
              aria-label={$_('media.local_search.rating_dimension')}
            >
              {#each availableRatingDimensions(filter) as dimension (dimension.key)}
                <option value={dimension.key}>{dimension.name}</option>
              {/each}
            </select>
            <select
              class="select select-sm"
              bind:value={filter.minimum}
              onchange={rerunSearch}
              aria-label={$_('media.local_search.minimum_rating')}
            >
              {#each Array.from({ length: 10 }, (_, index) => index + 1) as score (score)}
                <option value={score}>{$_('media.local_search.at_least_score', { values: { score } })}</option>
              {/each}
            </select>
            <button
              type="button"
              class="btn btn-square btn-ghost btn-sm"
              title={$_('media.local_search.remove_rating_filter')}
              aria-label={$_('media.local_search.remove_rating_filter')}
              onclick={() => removeRatingFilter(filter.id)}
            >
              <iconify-icon icon={icons.delete} width="1.1rem"></iconify-icon>
            </button>
          </div>
        {/each}
        {#if ratingFilters.length < ratingDimensions.length}
          <button type="button" class="btn w-fit btn-ghost btn-sm" onclick={addRatingFilter}>
            <iconify-icon icon={icons.addCircle} width="1.1rem"></iconify-icon>
            {$_('media.local_search.add_rating_filter')}
          </button>
        {/if}
      </fieldset>
      <SearchHistoryChips
        bind:this={history}
        relId={0}
        onselect={(value) => {
          searchValue = value;
          void search(value);
        }}
      />
    </div>

    {#if $loading}
      <div class="flex h-48 items-center justify-center"><span class="loading loading-lg loading-spinner"></span></div>
    {:else if results}
      {@const total = Object.values(results.totals).reduce((sum, value) => sum + value, 0)}
      {#if total === 0}
        <div class="flex h-48 items-center justify-center opacity-60">{$_('media.local_search.empty')}</div>
      {:else}
        {#each ['movies', 'tv_shows', 'episodes'] as group}
          {@const items = results[group as 'movies' | 'tv_shows' | 'episodes']}
          {#if items.length}
            <section class="mt-8">
              <h2 class="mb-3 text-lg font-semibold">
                {$_(`media.local_search.${group}`)}
                <span class="text-sm font-normal opacity-50"
                  >{results.totals[group as 'movies' | 'tv_shows' | 'episodes']}</span
                >
              </h2>
              <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {#each items as item (item.id)}
                  <button
                    class="flex min-w-0 gap-3 rounded-sm bg-base-200 p-2 text-left hover:bg-base-300"
                    onclick={() => goto(mediaPath(item))}
                  >
                    <div class="relative aspect-[2/3] w-16 shrink-0 overflow-hidden rounded-sm bg-base-300">
                      <RatingBadges values={item.ratings} class="absolute top-1 left-1 z-1" />
                      {#if item.poster}
                        <Image proxy="store" src={item.poster} width="100%" ratio="2/3" />
                      {:else}
                        <img
                          use:authenticatedMediaImage={item.id}
                          alt={item.name}
                          loading="lazy"
                          class="size-full object-cover"
                        />
                      {/if}
                    </div>
                    <span class="min-w-0 py-1">
                      <span class="line-clamp-2 font-medium">{item.name}</span>
                      {#if item.parent_name}<span class="mt-1 block truncate text-xs opacity-60"
                          >{item.parent_name}</span
                        >{/if}
                      <span class="mt-1 block truncate text-xs opacity-50"
                        >{item.lib_name}{item.year ? ` · ${item.year}` : ''}</span
                      >
                      {#if item.tags?.length}
                        <span class="mt-1 flex flex-wrap gap-1">
                          {#each item.tags as tag (tag)}
                            <span class="badge badge-soft badge-xs badge-primary">#{tag}</span>
                          {/each}
                        </span>
                      {/if}
                    </span>
                  </button>
                {/each}
              </div>
            </section>
          {/if}
        {/each}

        {#each ['books', 'images'] as group}
          {@const items = results[group as 'books' | 'images']}
          {#if items.length}
            <section class="mt-8">
              <h2 class="mb-3 text-lg font-semibold">
                {$_(`media.local_search.${group}`)}
                <span class="text-sm font-normal opacity-50">{results.totals[group as 'books' | 'images']}</span>
              </h2>
              <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
                {#each items as item (item.id)}
                  <button
                    class="group min-w-0 text-left"
                    onclick={() => goto(`/galleries/${item.gallery_id}/${item.id}`)}
                  >
                    <div class="relative aspect-square overflow-hidden rounded-sm bg-base-200">
                      <RatingBadges values={item.ratings} class="absolute top-1 left-1 z-1" />
                      <img
                        use:authenticatedImage={item.id}
                        alt={galleryTitle(item)}
                        loading="lazy"
                        class="size-full object-cover transition-transform group-hover:scale-105"
                      />
                    </div>
                    <span class="mt-1.5 block truncate text-sm font-medium">{galleryTitle(item)}</span>
                    {#if item.tags?.length}
                      <span class="mt-1 flex flex-wrap gap-1">
                        {#each item.tags as tag (tag)}
                          <span class="badge badge-soft badge-xs badge-primary">#{tag}</span>
                        {/each}
                      </span>
                    {/if}
                    <span class="block truncate text-xs opacity-50"
                      >{item.gallery_name}{item.item_count ? ` · ${item.item_count}` : ''}</span
                    >
                  </button>
                {/each}
              </div>
            </section>
          {/if}
        {/each}
      {/if}
    {:else if searched}
      <div class="flex h-48 items-center justify-center opacity-60">{$_('media.local_search.empty')}</div>
    {/if}
  </div>
</Container>
