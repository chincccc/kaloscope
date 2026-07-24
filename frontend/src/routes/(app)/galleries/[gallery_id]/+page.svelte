<script lang="ts">
  import { beforeNavigate, goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '$lib/api';
  import { authenticatedImage } from '$lib/authenticated-image';
  import {
    Container,
    DataView,
    MediaTagEditor,
    Paginator,
    RatingBadges,
    ResourceRenamer,
    Search,
    type PaginatorProps
  } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { captureScrollPosition, restorePosition, user } from '$lib/stores';
  import type { Page, RatingValue, Resp } from '$lib/types';
  import { onMount, tick, untrack } from 'svelte';
  import { MediaQuery } from 'svelte/reactivity';
  import { queryParameters, ssp } from 'sveltekit-search-params';

  type GalleryBook = {
    id: number;
    name: string | null;
    tags: string[];
    item_count: number;
    uncategorized: boolean;
    ratings?: RatingValue[];
  };
  type GalleryBookPage = Page<GalleryBook> & { scanning: boolean };

  const query = queryParameters(
    { page_num: ssp.number(0), page_size: ssp.number(0), keyword: ssp.string('') },
    { pushHistory: false }
  );
  const standaloneMode = new MediaQuery('(display-mode: standalone)');
  let view: DataView<GalleryBook>;
  let items: GalleryBook[] = $state([]);
  let pagination: Omit<PaginatorProps, 'current' | 'size'> = $state({ onchange: () => search(true) });
  const outerLoading = createLoading();
  const innerLoading = createLoading();
  let abortController: AbortController | null = null;
  let renamer: ResourceRenamer | null = $state(null);
  let tagEditor: MediaTagEditor | null = $state(null);
  let galleryId = $derived(page.params.gallery_id ?? '');
  let currentGalleryId: string | null = null;

  let scanTimer: ReturnType<typeof setTimeout> | null = null;

  beforeNavigate(({ from, to }) => captureScrollPosition(from, to, view, standaloneMode.current));

  function search(toTop: boolean = false, silent: boolean = false) {
    if (scanTimer) clearTimeout(scanTimer);
    scanTimer = null;
    let aborted = false;
    abortController?.abort();
    abortController = new AbortController();
    if (!silent) innerLoading.start();
    api
      .get('gallery/book/list', {
        signal: abortController.signal,
        searchParams: {
          page_num: query.page_num,
          page_size: query.page_size,
          gallery_id: galleryId,
          keyword: query.keyword
        }
      })
      .json<Resp<GalleryBookPage>>()
      .then(({ data }) => {
        if (data.scanning) {
          scanTimer = setTimeout(() => search(false, true), 1000);
        } else {
          scanTimer = null;
        }
        items = data.items;
        pagination.total = data.total;
      })
      .catch((error) => {
        aborted = error.name === 'AbortError';
        if (!aborted && !silent) {
          items = [];
          pagination.total = 0;
        }
      })
      .finally(() => {
        if (!aborted && !silent) {
          innerLoading.end();
          outerLoading.end();
          tick().then(() => restorePosition(standaloneMode.current ? view : window, toTop));
        }
      });
  }

  $effect(() => {
    if (galleryId !== currentGalleryId) {
      untrack(() => {
        outerLoading.start();
        const params = page.url.searchParams;
        query.keyword = params.get('keyword') || '';
        query.page_num = Number(params.get('page_num')) || 1;
        query.page_size = Number(params.get('page_size')) || 40;
        search();
        currentGalleryId = galleryId;
      });
    }
  });

  onMount(() => {
    const refresh = () => {
      if (document.visibilityState === 'visible') search(false, true);
    };
    const timer = setInterval(refresh, 30_000);
    window.addEventListener('focus', refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener('focus', refresh);
      if (scanTimer) clearTimeout(scanTimer);
      abortController?.abort();
    };
  });
</script>

<Container class="pull-to-refresh" loading={$outerLoading}>
  <DataView
    bind:this={view}
    mode="grid"
    data={items}
    loading={$innerLoading}
    hideOnScroll={standaloneMode.current}
    filtersClass="sm:justify-center"
    gridClass="grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3"
    itemClass="min-w-0"
  >
    {#snippet filters()}
      <Search
        label={$_('field.keyword')}
        bind:value={query.keyword}
        onsearch={() => {
          query.page_num = 1;
          search(true);
        }}
        maxWidth="36rem"
      />
    {/snippet}

    {#snippet item(item)}
      {@const title = item.uncategorized ? $_('gallery.uncategorized') : item.name || ''}
      <div class="group relative">
        <button
          class="relative aspect-[2/3] w-full overflow-hidden rounded-field bg-base-200 shadow-sm transition-shadow hover:shadow-lg"
          onclick={() => goto(`/galleries/${galleryId}/${item.id}`)}
          {title}
        >
          <RatingBadges values={item.ratings} class="absolute top-1 left-1 z-1" />
          <img
            use:authenticatedImage={{
              key: item.id,
              load: (signal) => api.get(`gallery/cover/${item.id}`, { signal }).blob()
            }}
            alt={title}
            loading="lazy"
            class="size-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
          <span
            class="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-black/70 px-2 py-1.5 text-left text-xs text-white"
          >
            <span class="min-w-0 flex-1 truncate">{title}</span>
            <span class="shrink-0 tabular-nums opacity-70">{item.item_count}</span>
          </span>
        </button>
        {#if $user?.role === 'admin' && !item.uncategorized}
          <button
            class="btn absolute top-1 right-1 z-10 btn-circle border-0 bg-black/60 text-white opacity-100 transition-opacity btn-sm sm:opacity-0 sm:group-hover:opacity-100"
            title={$_('action.rename')}
            aria-label={$_('action.rename')}
            onclick={(event) => {
              event.stopPropagation();
              renamer?.showModal({
                endpoint: 'gallery/book/' + item.id + '/rename',
                name: item.name || ''
              });
            }}
          >
            <iconify-icon icon={icons.edit} width="1rem"></iconify-icon>
          </button>
          <button
            class="btn absolute top-11 right-1 z-10 btn-circle border-0 bg-black/60 text-white opacity-100 transition-opacity btn-sm sm:opacity-0 sm:group-hover:opacity-100"
            title={$_('media.edit_tags')}
            aria-label={$_('media.edit_tags')}
            onclick={(event) => {
              event.stopPropagation();
              tagEditor?.showModal({
                endpoint: 'gallery/book/' + item.id + '/tags',
                tags: item.tags
              });
            }}
          >
            <iconify-icon icon={icons.addCircle} width="1rem"></iconify-icon>
          </button>
        {/if}
        {#if item.tags.length}
          <div class="mt-1 flex flex-wrap gap-1">
            {#each item.tags as tag (tag)}
              <span class="badge badge-soft badge-xs badge-primary">#{tag}</span>
            {/each}
          </div>
        {/if}
      </div>
    {/snippet}

    {#snippet paginator(disabled)}
      <Paginator {disabled} {...pagination} bind:current={query.page_num} size={query.page_size} />
    {/snippet}
  </DataView>
</Container>

<ResourceRenamer bind:this={renamer} onsave={() => search()} />
<MediaTagEditor bind:this={tagEditor} onsave={() => search()} />
