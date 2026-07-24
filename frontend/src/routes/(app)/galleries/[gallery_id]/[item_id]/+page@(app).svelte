<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '$lib/api';
  import { ImageViewer, Overlay, ResourceRatings } from '$lib/components';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { Chapter, Resp } from '$lib/types';
  import { onMount, tick } from 'svelte';

  type ReaderItem = {
    id: number;
    dir: string;
    name: string;
  };

  type ReaderContext = {
    title: string | null;
    uncategorized: boolean;
    chapter_id: string;
    chapters: (Chapter & { unfiled?: boolean })[];
    items: ReaderItem[];
    current_index: number;
  };

  const INITIAL_IMAGE_COUNT = 3;
  const LOAD_BATCH_SIZE = 1;
  const entryItemId = page.params.item_id;
  let imageViewer: ImageViewer;
  let loading = $state(true);
  let failed = $state(false);
  let ratingsOpen = $state(false);
  let controller: AbortController | null = null;
  const objectUrls = new Set<string>();

  function releaseObjectUrls() {
    for (const url of objectUrls) URL.revokeObjectURL(url);
    objectUrls.clear();
  }

  async function fetchImages(items: ReaderItem[], signal: AbortSignal) {
    const blobs = await Promise.all(items.map((item) => api.get(`gallery/image/${item.id}`, { signal }).blob()));
    return blobs.map((blob) => URL.createObjectURL(blob));
  }

  async function loadChapter(itemId: number, replaceState = false) {
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    loading = true;
    failed = false;

    try {
      const { data } = await api.get(`gallery/reader/${itemId}`, { signal }).json<Resp<ReaderContext>>();
      const initialSize = Math.min(
        data.items.length,
        Math.max(INITIAL_IMAGE_COUNT, data.current_index + INITIAL_IMAGE_COUNT)
      );
      const initialUrls = await fetchImages(data.items.slice(0, initialSize), signal);
      if (signal.aborted) {
        for (const url of initialUrls) URL.revokeObjectURL(url);
        return;
      }

      releaseObjectUrls();
      for (const url of initialUrls) objectUrls.add(url);
      const title = data.uncategorized ? $_('gallery.uncategorized') : data.title || '';
      const chapters = data.chapters.map((chapter) => ({
        ...chapter,
        title: chapter.unfiled ? $_('gallery.unfiled_chapter') : chapter.title || title
      }));
      await tick();
      imageViewer.mount({
        images: initialUrls,
        image_count: data.items.length,
        initialIndex: data.current_index,
        title,
        chapters,
        chapterId: data.chapter_id,
        chapterChange: (chapter) => {
          if (chapter.id) void loadChapter(Number(chapter.id), true);
        },
        loadMore: async (offset) => {
          const urls = await fetchImages(data.items.slice(offset - 1, offset - 1 + LOAD_BATCH_SIZE), signal);
          if (signal.aborted) {
            for (const url of urls) URL.revokeObjectURL(url);
            return null;
          }
          for (const url of urls) objectUrls.add(url);
          return { images: urls, image_count: data.items.length };
        }
      });

      if (replaceState) {
        const url = new URL(page.url);
        url.pathname = `/galleries/${page.params.gallery_id}/${entryItemId}`;
        url.searchParams.set('chapter_id', String(itemId));
        await goto(url, {
          replaceState: true,
          keepFocus: true,
          noScroll: true
        });
      }
    } catch (error) {
      if ((error as Error).name !== 'AbortError') failed = true;
    } finally {
      if (!signal.aborted) loading = false;
    }
  }

  onMount(() => {
    const chapterId = page.url.searchParams.get('chapter_id') || entryItemId;
    void loadChapter(Number(chapterId));
    return () => {
      controller?.abort();
      releaseObjectUrls();
    };
  });
</script>

<div class="history-back fixed inset-0 layer-1">
  {#if loading}<Overlay black loading />{/if}
  {#if failed}
    <div class="fixed inset-0 z-2 flex-center bg-black text-white/70">
      {$_('alert.internal_server_error')}
    </div>
  {/if}
  <ImageViewer bind:this={imageViewer} />
  <button
    class="btn fixed top-1.5 right-12 z-4 border-0 bg-black/50 text-white/80 shadow-none btn-ghost btn-xs"
    class:btn-active={ratingsOpen}
    aria-label={$_('rating.title')}
    title={$_('rating.title')}
    onclick={() => (ratingsOpen = !ratingsOpen)}
  >
    <iconify-icon icon={icons.starFilled} width="1.25rem"></iconify-icon>
  </button>
  {#if ratingsOpen}
    <button
      class="fixed inset-0 z-2 bg-black/10"
      aria-label={$_('action.close')}
      onclick={() => (ratingsOpen = false)}
    ></button>
    <ResourceRatings
      resourceType="gallery_book"
      resourceId={Number(entryItemId)}
      dark
      class="fixed top-12 right-2 z-4 w-[min(22rem,calc(100vw-1rem))] shadow-xl"
    />
  {/if}
</div>
