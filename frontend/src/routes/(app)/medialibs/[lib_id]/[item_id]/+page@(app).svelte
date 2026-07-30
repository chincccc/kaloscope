<script lang="ts">
  import { page } from '$app/state';
  import { api } from '$lib/api';
  import {
    Backdrop,
    Container,
    Image,
    ImageViewer,
    MediaActions,
    mediaTitle,
    Rating,
    ResourceRatings,
    VideoPlayer
  } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { formatMediaBitrate, formatMediaDuration, formatMediaResolution, formatMediaSize } from '$lib/media-format';
  import { historyBack, user } from '$lib/stores';
  import type { MediaItem, MediaMeta, Resp } from '$lib/types';
  import { buildStreamUrl } from '$lib/utils';
  import { onDestroy, onMount, tick } from 'svelte';

  type Screenshot = {
    index: number;
    position: number;
    url: string;
  };

  type ScreenshotStatus = {
    count: number;
    items: Array<{ index: number; position: number; url: string }>;
    pending: boolean;
    error: boolean;
  };

  // the loading state
  const loading = createLoading();

  // the parent media item and its metadata
  let media: MediaItem | null = $state(null);
  let meta: MediaMeta | null = $state(null);

  // the selected child media item and its metadata
  let _media: MediaItem | null = $state(null);
  let _meta: MediaMeta | null = $state(null);
  let technicalItems = $derived.by(() => {
    if (!media) return [];
    return [
      {
        label: media.episode_count ? $_('media.total_duration') : $_('media.duration'),
        value: formatMediaDuration(media.duration)
      },
      { label: $_('media.resolution'), value: formatMediaResolution(media.width, media.height) },
      { label: $_('media.file_size'), value: formatMediaSize(media.size) },
      { label: $_('media.bitrate'), value: formatMediaBitrate(media.bitrate) }
    ].filter((item) => item.value);
  });

  // the player instance and playing state
  let player: VideoPlayer | null = $state(null);
  let playing = $state(false);
  let screenshotViewer: ImageViewer | null = $state(null);
  let screenshotViewerOpen = $state(false);
  let screenshots = $state<Screenshot[]>([]);
  let screenshotTitle = $state('');
  let screenshotExpected = $state(0);
  let screenshotLoading = $state(false);
  let screenshotController: AbortController | null = null;
  let technicalTimer: ReturnType<typeof setTimeout> | null = null;
  let screenshotGeneration = 0;

  // the sorted child media items
  let parts: MediaItem[] = $derived.by(() => {
    const items = media?.children;
    if (!items || items.length === 0) {
      return [];
    }
    return items
      .filter((i) => i.visible)
      .sort((a, b) => {
        if (a.season !== b.season) {
          return (a.season ?? 0) - (b.season ?? 0);
        }
        if (a.episode !== b.episode) {
          return (a.episode ?? 0) - (b.episode ?? 0);
        }
        return (a.title ?? a.name).localeCompare(b.title ?? b.name, undefined, {
          numeric: true,
          sensitivity: 'base'
        });
      });
  });

  /**
   * Start playing the selected media item.
   */
  function play() {
    const target = _media ?? media;
    if (!target) {
      return;
    }
    playing = true;
    tick().then(() => {
      const chapters = [];
      if (parts.length) {
        for (const part of parts) {
          chapters.push({
            url: buildStreamUrl(part.path),
            title: mediaTitle(part)
          });
        }
      }
      player?.mount({
        url: buildStreamUrl(target.path),
        back: () => (playing = false),
        title: mediaTitle(target),
        chapters: chapters
      });
    });
  }

  /**
   * Get the media item details by ID.
   *
   * @param id - The media item ID.
   * @return The media item details.
   */
  async function getDetails(id: number): Promise<MediaItem> {
    const resp = await api.get(`media/${id}`).json<Resp<MediaItem>>();
    return resp.data;
  }

  function clearScreenshots() {
    screenshotGeneration++;
    screenshotController?.abort();
    screenshotController = null;
    for (const screenshot of screenshots) URL.revokeObjectURL(screenshot.url);
    screenshots = [];
    screenshotTitle = '';
    screenshotExpected = 0;
    screenshotLoading = false;
    screenshotViewerOpen = false;
  }

  async function loadScreenshots(target: MediaItem) {
    clearScreenshots();
    const generation = screenshotGeneration;
    const controller = new AbortController();
    screenshotController = controller;
    screenshotLoading = true;
    // eslint-disable-next-line svelte/prefer-svelte-reactivity
    const loaded = new Map<number, Screenshot>();

    try {
      while (!controller.signal.aborted) {
        const response = await api
          .get(`media/${target.id}/screenshots`, { signal: controller.signal })
          .json<Resp<ScreenshotStatus>>();
        const status = response.data;
        if (generation !== screenshotGeneration) return;
        screenshotExpected = status.count;
        if (status.count === 0) return;
        screenshotTitle = mediaTitle(target);

        await Promise.all(
          status.items
            .filter((item) => !loaded.has(item.index))
            .map(async (item) => {
              const blob = await api.get(item.url, { signal: controller.signal }).blob();
              const url = URL.createObjectURL(blob);
              if (generation !== screenshotGeneration || controller.signal.aborted) {
                URL.revokeObjectURL(url);
                return;
              }
              loaded.set(item.index, { ...item, url });
            })
        );
        screenshots = [...loaded.values()].sort((a, b) => a.index - b.index);
        screenshotLoading = status.pending;
        if (!status.pending || status.error) return;
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, 1000);
          controller.signal.addEventListener(
            'abort',
            () => {
              window.clearTimeout(timer);
              resolve();
            },
            { once: true }
          );
        });
      }
    } catch (error) {
      if (!controller.signal.aborted) console.error(error);
    } finally {
      if (generation === screenshotGeneration) {
        screenshotLoading = false;
        screenshotController = null;
      }
    }
  }

  function clearSelectedMedia() {
    if (!_media) return;
    _media = null;
    _meta = null;
    clearScreenshots();
  }

  function formatTimestamp(position: number): string {
    const seconds = Math.round(position);
    const minutes = Math.floor(seconds / 60);
    return `${Math.floor(minutes / 60)
      .toString()
      .padStart(2, '0')}:${(minutes % 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
  }

  function showScreenshot(index: number) {
    screenshotViewerOpen = true;
    tick().then(() => {
      screenshotViewer?.mount({
        images: screenshots.map((screenshot) => screenshot.url),
        image_count: screenshots.length,
        initialIndex: index,
        title: screenshotTitle
      });
    });
  }

  /**
   * Select a child media item and load its details.
   *
   * @param item - The child media item.
   */
  async function selectMedia(item: MediaItem) {
    if (_media?.id === item.id) {
      return;
    }
    try {
      const data = await getDetails(item.id);
      _media = data;
      _meta = data.metadata ?? null;
      void loadScreenshots(data);
    } catch (error) {
      console.error(error);
    }
  }
  function applyParentDetails(data: MediaItem) {
    media = data;
    meta = data.metadata ?? null;
    if (technicalTimer) clearTimeout(technicalTimer);
    technicalTimer = null;
    if (data.technical_pending) {
      technicalTimer = setTimeout(() => {
        getDetails(data.id)
          .then((refreshed) => {
            if (media?.id === data.id) applyParentDetails(refreshed);
          })
          .catch((error) => console.error(error));
      }, 1000);
    }
  }

  async function refreshDetails() {
    if (!media) return;
    const data = await getDetails(media.id);
    applyParentDetails(data);
  }

  // load the parent media item details on mount
  onMount(() => {
    loading.start();
    getDetails(Number(page.params.item_id))
      .then(async (data) => {
        applyParentDetails(data);
        if (!data.children?.length) {
          void loadScreenshots(data);
        } else {
          const episodeId = Number(page.url.searchParams.get('episode_id'));
          const episode = data.children.find((item) => item.id === episodeId);
          if (episode) await selectMedia(episode);
        }
      })
      .finally(() => {
        loading.end();
      });
  });
  onDestroy(() => {
    if (technicalTimer) clearTimeout(technicalTimer);
    clearScreenshots();
  });
</script>

<svelte:document
  onclick={(event) => {
    // clear the selected child media item when clicking outside
    if (!screenshotViewerOpen && !(event.target as Element).closest('.media-part, .media-screenshot')) {
      clearSelectedMedia();
    }
  }}
/>

<Container class="pull-to-refresh history-back navbar-hidden" loading={$loading}>
  {#if media}
    <!-- backdrop -->
    <Backdrop
      proxy="store"
      opacity="0.3"
      src={_media?.backdrop ?? media?.backdrop ?? _media?.poster ?? media?.poster}
    />

    <!-- back button -->
    <button
      class="btn absolute top-2 left-2 z-1 btn-circle size-10 bg-blur-80 btn-ghost"
      aria-label="Back"
      onclick={historyBack}
    >
      <iconify-icon icon={icons.backSolid} width="1.25rem" class="opacity-80"></iconify-icon>
    </button>

    {#if $user?.role === 'admin'}
      <MediaActions
        item={media}
        class="absolute dropdown-end top-2 right-2 z-1"
        triggerClass="size-10 bg-blur-80"
        onedit={refreshDetails}
        onrename={refreshDetails}
        ontag={refreshDetails}
        onscrape={refreshDetails}
      />
    {/if}

    <!-- main content -->
    <div class="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6">
      <div class="flex flex-col gap-6 sm:flex-row">
        <!-- poster -->
        <div class="relative self-center sm:self-start">
          <Image proxy="store" src={media?.poster} width="14rem" ratio="2/3" class="shadow-lg" />
          {#if !media.children || media.children.length === 0}
            <div class="absolute inset-0 flex-center">
              <button
                class="group btn btn-circle size-20 btn-enlarge bg-black/30 text-white/60"
                aria-label="Play"
                onclick={play}
              >
                <iconify-icon icon={icons.play} width="2.5rem"> </iconify-icon>
              </button>
            </div>
          {/if}
        </div>

        <div class="flex min-w-0 flex-1 flex-col gap-3">
          <!-- titles -->
          <h1 class="text-2xl font-bold sm:text-3xl">{media?.title ?? media?.name}</h1>
          {#if meta?.originaltitle && meta.originaltitle !== meta.title}
            <h4 class="text-sm opacity-60">{meta.originaltitle}</h4>
          {/if}

          <!-- badges -->
          <div class="flex flex-wrap gap-2">
            {#if media.year}
              <span class="badge badge-outline">{media.year}</span>
            {/if}
            {#if meta?.mpaa}
              <span class="badge badge-outline">{meta.mpaa}</span>
            {/if}
            {#if meta?.country}
              <span class="badge badge-outline">{meta.country}</span>
            {/if}
            <Rating score={media.rating} class="h-6 border" />
            {#each media.tags ?? [] as tag (tag)}
              <span class="badge badge-soft badge-primary">#{tag}</span>
            {/each}
          </div>
          {#if technicalItems.length}
            <div class="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
              {#each technicalItems as item (item.label)}
                <div class="min-w-0">
                  <div class="text-xs opacity-50">{item.label}</div>
                  <div class="truncate text-sm font-medium tabular-nums">{item.value}</div>
                </div>
              {/each}
            </div>
          {/if}

          <ResourceRatings resourceType="media" resourceId={media.id} class="mt-1 max-w-xl" />

          <!-- tagline -->
          {#if meta?.tagline}
            <p class="text-sm italic opacity-70">{meta.tagline}</p>
          {/if}

          <!-- genres -->
          {#if meta?.genres?.length}
            <div class="flex flex-wrap gap-1.5">
              {#each meta.genres as genre, i (i)}
                <span class="badge badge-sm opacity-80 badge-primary">{genre}</span>
              {/each}
            </div>
          {/if}

          <!-- plot -->
          {#if _media && _meta?.plot}
            <div class="mt-2 font-semibold text-surface">
              {mediaTitle(_media)}
            </div>
          {/if}
          <p class="mt-1 text-sm leading-relaxed opacity-80">{_meta?.plot ?? meta?.plot}</p>
        </div>
      </div>

      <!-- staff -->
      {#if meta?.directors?.length || meta?.writers?.length || meta?.studios?.length}
        {@const cols = [meta?.directors, meta?.writers, meta?.studios].filter((arr) => arr?.length).length}
        <div class="mt-6 grid gap-3 max-sm:grid-cols-1!" style="grid-template-columns: repeat({cols}, minmax(0, 1fr))">
          {#if meta?.directors?.length}
            <div>
              <span class="font-semibold text-primary/80">{$_('media.director')}</span>
              <p class="text-sm opacity-70">{meta.directors.join(', ')}</p>
            </div>
          {/if}
          {#if meta?.writers?.length}
            <div>
              <span class="font-semibold text-primary/80">{$_('media.writer')}</span>
              <p class="text-sm opacity-70">{meta.writers.join(', ')}</p>
            </div>
          {/if}
          {#if meta?.studios?.length}
            <div>
              <span class="font-semibold text-primary/80">{$_('media.studio')}</span>
              <p class="text-sm opacity-70">{meta.studios.join(', ')}</p>
            </div>
          {/if}
        </div>
      {/if}

      <!-- actors -->
      {#if meta?.actors?.length}
        <div class="mt-6">
          <h2 class="mb-3 text-lg font-semibold">{$_('media.cast')}</h2>
          <div
            class="flex gap-3 overflow-x-auto pb-3"
            onwheel={(event) => {
              event.preventDefault();
              event.currentTarget.scrollLeft += event.deltaY;
            }}
          >
            {#each meta.actors as actor, i (i)}
              <div class="flex w-24 shrink-0 flex-col items-center gap-1 text-center">
                <Image proxy="store" src={actor.thumb} text={actor.name} width="4.5rem" circle />
                <div class="line-clamp-1 text-xs font-medium" title={actor.name}>{actor.name}</div>
                <div class="line-clamp-1 text-xs opacity-50" title={actor.role}>{actor.role}</div>
              </div>
            {/each}
          </div>
        </div>
      {/if}

      {#if screenshotTitle}
        <section class="media-screenshot mt-6">
          <div class="mb-3 flex min-h-7 items-center justify-between gap-3">
            <h2 class="text-lg font-semibold">{$_('media.screenshots')}</h2>
            <div class="flex items-center gap-2 text-xs tabular-nums opacity-60">
              <span>{screenshots.length} / {screenshotExpected}</span>
              {#if screenshotLoading}<span class="loading loading-xs loading-spinner"></span>{/if}
            </div>
          </div>
          {#if screenshots.length}
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {#each screenshots as screenshot, index (screenshot.position)}
                <button
                  class="group relative aspect-video w-full overflow-hidden rounded-sm bg-base-300 shadow-sm"
                  aria-label={`${$_('media.screenshot')} ${index + 1}`}
                  onclick={() => showScreenshot(index)}
                >
                  <Image
                    src={screenshot.url}
                    width="100%"
                    ratio="16/9"
                    class="transition-transform group-hover:scale-105"
                  />
                  <span class="absolute right-1.5 bottom-1.5 rounded-sm bg-black/65 px-1.5 py-0.5 text-xs text-white">
                    {formatTimestamp(screenshot.position)}
                  </span>
                </button>
              {/each}
            </div>
          {:else if screenshotLoading}
            <div class="flex h-28 items-center justify-center">
              <span class="loading loading-md loading-spinner opacity-60"></span>
            </div>
          {/if}
        </section>
      {/if}

      <!-- parts -->
      {#if parts.length}
        <div class="mt-6">
          <h2 class="mb-3 text-lg font-semibold">
            {media.lib?.lib_type === 'tv_show' ? $_('media.episodes') : $_('media.parts')}
          </h2>
          <div class="flex max-h-144 flex-col gap-2 overflow-y-scroll px-2 py-3">
            {#each parts as part (part.id)}
              {@const active = _media?.id === part.id}
              {@const activeClass = active ? 'bg-primary/15' : 'bg-gradient hover:bg-base-content/15'}
              {@const transClass = 'transition-colors duration-300'}
              <button
                class="media-part flex items-center rounded-lg px-3 py-2 text-left {transClass} {activeClass}"
                onclick={() => selectMedia(part)}
              >
                <Image proxy="store" src={part.poster} text={part.name} width="5rem" ratio="16/9" />
                <div class="flex min-w-0 flex-1 flex-col gap-0.5 px-3">
                  <span class="truncate text-sm font-medium {transClass}" class:text-primary={active}>
                    {mediaTitle(part)}
                  </span>
                  <span class="text-xs opacity-50">{part.aired}</span>
                  {#if part.tags?.length}
                    <span class="flex flex-wrap gap-1">
                      {#each part.tags as tag (tag)}
                        <span class="badge badge-soft badge-xs badge-primary">#{tag}</span>
                      {/each}
                    </span>
                  {/if}
                </div>
                {#if part.duration}
                  <span class="shrink-0 text-xs tabular-nums opacity-60">{formatMediaDuration(part.duration)}</span>
                {/if}
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <div
                  tabindex="0"
                  role="button"
                  class="btn btn-circle btn-enlarge shadow-sm btn-sm {transClass}"
                  class:btn-active={active}
                  class:btn-subtle={!active}
                  onclick={(event) => {
                    event.stopPropagation();
                    selectMedia(part).then(play);
                  }}
                >
                  <iconify-icon icon={icons.play} width="1.25rem"></iconify-icon>
                </div>
                {#if $user?.role === 'admin'}
                  <MediaActions
                    item={part}
                    class="dropdown-end ml-1"
                    triggerClass="opacity-70"
                    onclick={() => {
                      selectMedia(part);
                    }}
                    onedit={() => {
                      Promise.all([getDetails(media!.id), getDetails(part.id)]).then(([parent, episode]) => {
                        media = parent;
                        meta = parent.metadata ?? null;
                        if (_media?.id === part.id) {
                          _media = episode;
                          _meta = episode.metadata ?? null;
                        }
                      });
                    }}
                    onrename={() => {
                      Promise.all([getDetails(media!.id), getDetails(part.id)]).then(([parent, episode]) => {
                        media = parent;
                        meta = parent.metadata ?? null;
                        if (_media?.id === part.id) {
                          _media = episode;
                          _meta = episode.metadata ?? null;
                        }
                      });
                    }}
                    ontag={() => {
                      Promise.all([getDetails(media!.id), getDetails(part.id)]).then(([parent, episode]) => {
                        media = parent;
                        meta = parent.metadata ?? null;
                        if (_media?.id === part.id) {
                          _media = episode;
                          _meta = episode.metadata ?? null;
                        }
                      });
                    }}
                    ondelete={() => {
                      // refresh the parent media details to update the parts list
                      getDetails(media!.id).then((data) => {
                        media = data;
                      });
                    }}
                  />
                {/if}
              </button>
            {/each}
          </div>
        </div>
      {/if}

      <!-- tags -->
      {#if meta?.tags?.length}
        <div class="mt-6">
          <h2 class="mb-3 text-lg font-semibold">{$_('media.tags')}</h2>
          <div class="flex flex-wrap gap-1.5">
            {#each meta.tags as tag, i (i)}
              <span class="badge badge-soft badge-sm text-base-content/70">{tag}</span>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  {/if}
</Container>

<!-- player overlay -->
{#if playing}
  <div class="fixed inset-0 layer-1 max-sm:bottom-(--ks-dock-h)">
    <VideoPlayer bind:this={player} />
  </div>
{/if}

{#if screenshotViewerOpen}
  <div class="fixed inset-0 layer-2">
    <ImageViewer bind:this={screenshotViewer} onback={() => (screenshotViewerOpen = false)} />
  </div>
{/if}
