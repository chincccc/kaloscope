<script lang="ts">
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import FeedPlayer from '$lib/components/business/feed/FeedPlayer.svelte';
  import { Container } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { histories, historyBack, user } from '$lib/stores';
  import type { Resp } from '$lib/types';
  import { onMount } from 'svelte';

  type FeedItem = {
    id: number;
    lib_id: number;
    lib_type: 'movie' | 'tv_show';
    path: string;
    name: string;
    title: string | null;
    poster: string | null;
    backdrop: string | null;
    season: number | null;
    episode: number | null;
    parent_id: number | null;
    parent_name: string | null;
  };

  type FeedReturnState = {
    current: FeedItem;
    queued: FeedItem | null;
    previous: FeedItem[];
    seen: number[];
    positions: Array<[number, number]>;
  };

  const returnStateKey = 'kaloscope.feed.return-state';
  const loading = createLoading();
  let current: FeedItem | null = $state(null);
  let queued: FeedItem | null = null;
  let player: FeedPlayer | null = $state(null);
  let previous = $state<FeedItem[]>([]);
  let seen: number[] = [];
  let switching = $state(false);
  let direction = $state<'next' | 'previous'>('next');
  let touchX = 0;
  let touchY = 0;
  let wheelLocked = false;
  // eslint-disable-next-line svelte/prefer-svelte-reactivity
  const positions = new Map<number, number>();
  let destroyed = false;
  let resolveReady: (() => void) | null = null;
  // eslint-disable-next-line svelte/prefer-svelte-reactivity
  const controllers = new Set<AbortController>();

  function displayTitle(item: FeedItem) {
    const title = item.title ?? item.name;
    if (item.season !== null && item.episode !== null) {
      return `S${item.season}E${item.episode} - ${title}`;
    }
    return title;
  }

  async function randomItem(): Promise<FeedItem | null> {
    const controller = new AbortController();
    controllers.add(controller);
    try {
      const response = await api
        .get('media/feed', {
          signal: controller.signal,
          searchParams: { exclude: seen.slice(-100).join(',') }
        })
        .json<Resp<FeedItem | null>>();
      const item = response.data;
      if (item && !seen.includes(item.id)) seen = [...seen, item.id];
      return item;
    } finally {
      controllers.delete(controller);
    }
  }

  function rememberPosition() {
    if (!current || !player) return;
    const position = player.playbackPosition();
    if (Number.isFinite(position) && position >= 0) {
      positions.set(current.id, position);
    }
  }

  function playerReady() {
    switching = false;
    resolveReady?.();
    resolveReady = null;
  }

  async function show(item: FeedItem, nextDirection: 'next' | 'previous') {
    switching = true;
    rememberPosition();
    direction = nextDirection;
    player = null;
    const ready = new Promise<void>((resolve) => {
      resolveReady = resolve;
    });
    current = item;
    await ready;
  }

  async function prefetch() {
    if (!queued && !destroyed) queued = await randomItem();
  }

  async function next() {
    if (switching) return;
    switching = true;
    try {
      const item = queued ?? (await randomItem());
      if (!item) return;
      queued = null;
      if (current) previous = [...previous, current].slice(-100);
      await show(item, 'next');
      void prefetch();
    } finally {
      switching = false;
    }
  }

  async function back() {
    if (switching || previous.length === 0) return;
    const items = [...previous];
    const item = items.pop();
    if (!item) return;
    previous = items;
    queued = current;
    await show(item, 'previous');
  }

  function saveReturnState() {
    if (!current) return;
    rememberPosition();
    const state: FeedReturnState = {
      current,
      queued,
      previous,
      seen,
      positions: [...positions.entries()]
    };
    sessionStorage.setItem(returnStateKey, JSON.stringify(state));
  }

  function restoreReturnState(): FeedReturnState | null {
    const value = sessionStorage.getItem(returnStateKey);
    sessionStorage.removeItem(returnStateKey);
    if (!value) return null;
    try {
      return JSON.parse(value) as FeedReturnState;
    } catch {
      return null;
    }
  }

  function episodeDetails() {
    if (!current?.parent_id) return;
    saveReturnState();
    goto(`/medialibs/${current.lib_id}/${current.parent_id}?episode_id=${current.id}`);
  }

  function leaveFeed() {
    if ($histories?.['/feed']) historyBack();
    else goto('/medialibs');
  }

  function touchStart(event: TouchEvent) {
    if (event.touches.length !== 1) return;
    touchX = event.touches[0].clientX;
    touchY = event.touches[0].clientY;
  }

  function touchEnd(event: TouchEvent) {
    const touch = event.changedTouches[0];
    if (!touch) return;
    const dx = touch.clientX - touchX;
    const dy = touch.clientY - touchY;
    const horizontal = Math.abs(dx) > Math.abs(dy) * 1.2;
    const vertical = Math.abs(dy) > Math.abs(dx) * 1.2;
    if (vertical && Math.abs(dy) >= 72) {
      dy < 0 ? void next() : void back();
    } else if (horizontal && dx <= -72 && current?.parent_id) {
      episodeDetails();
    }
  }

  function wheel(event: WheelEvent) {
    if (wheelLocked || Math.abs(event.deltaY) < 30) return;
    wheelLocked = true;
    event.deltaY > 0 ? void next() : void back();
    window.setTimeout(() => (wheelLocked = false), 700);
  }

  function keydown(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') void next();
    else if (event.key === 'ArrowUp') void back();
    else if (event.key === 'ArrowLeft' && current?.parent_id) episodeDetails();
  }

  onMount(() => {
    loading.start();
    window.addEventListener('keydown', keydown);
    const restored = restoreReturnState();
    const start = async () => {
      if (restored) {
        queued = restored.queued;
        previous = restored.previous;
        seen = restored.seen;
        positions.clear();
        for (const [id, position] of restored.positions) {
          positions.set(id, position);
        }
        await show(restored.current, 'next');
        if (!queued) void prefetch();
      } else {
        const item = await randomItem();
        if (item) {
          await show(item, 'next');
          void prefetch();
        }
      }
    };
    void start().finally(() => loading.end());
    return () => {
      destroyed = true;
      controllers.forEach((controller) => controller.abort());
      window.removeEventListener('keydown', keydown);
    };
  });
</script>

<Container class="history-back navbar-hidden p-0!">
  <div
    class="fixed inset-0 bottom-(--ks-dock-h) layer-1 overflow-hidden bg-black text-white sm:bottom-0"
    role="application"
    ontouchstart={touchStart}
    ontouchend={touchEnd}
    onwheel={wheel}
  >
    <button
      class="btn absolute top-3 left-3 z-4 hidden btn-circle size-10 border-0 bg-black/35 text-white sm:flex"
      aria-label="Back"
      onclick={leaveFeed}
    >
      <iconify-icon icon={icons.backSolid} width="1.25rem"></iconify-icon>
    </button>

    {#if $loading}
      <div class="pointer-events-none absolute inset-0 z-3 flex items-center justify-center bg-black">
        <span class="loading loading-lg loading-spinner text-white/70"></span>
      </div>
    {/if}

    {#if current}
      {#key current.id}
        <div class="absolute inset-0 {direction === 'next' ? 'feed-next' : 'feed-previous'}">
          <FeedPlayer
            bind:this={player}
            path={current.path}
            title={displayTitle(current)}
            uploader={current.parent_name ?? ''}
            startTime={positions.get(current.id)}
            randomStart={!positions.has(current.id) && Boolean($user?.preferences?.feed_random_start)}
            muted={false}
            onready={playerReady}
          />
        </div>
      {/key}

      <div
        class="pointer-events-none absolute inset-x-0 bottom-14 z-1 bg-linear-to-t from-black/75 to-transparent px-4 pt-16 pb-3 sm:bottom-12"
      >
        {#if current.parent_name}
          <div class="mb-1 text-sm font-medium opacity-70">{current.parent_name}</div>
        {/if}
        <h1 class="line-clamp-2 max-w-3xl text-base font-semibold sm:text-lg">{displayTitle(current)}</h1>
      </div>

      <div class="absolute right-3 bottom-28 z-2 flex flex-col gap-2 sm:bottom-24">
        <button
          class="btn btn-circle size-11 border-0 bg-black/35 text-white disabled:opacity-30"
          aria-label={$_('media.feed.previous')}
          disabled={previous.length === 0 || switching}
          onclick={() => void back()}
        >
          <iconify-icon icon={icons.arrowPreviousFilled} width="1.4rem" class="rotate-90"></iconify-icon>
        </button>
        <button
          class="btn btn-circle size-11 border-0 bg-black/35 text-white"
          aria-label={$_('media.feed.next')}
          disabled={switching}
          onclick={() => void next()}
        >
          <iconify-icon icon={icons.arrowNextFilled} width="1.4rem" class="rotate-90"></iconify-icon>
        </button>
        {#if current.parent_id}
          <button
            class="btn btn-circle size-11 border-0 bg-black/35 text-white"
            aria-label={$_('media.feed.episode_details')}
            onclick={episodeDetails}
          >
            <iconify-icon icon={icons.appsListDetail} width="1.4rem"></iconify-icon>
          </button>
        {/if}
      </div>
    {:else if !$loading}
      <div class="flex size-full items-center justify-center text-sm opacity-60">{$_('media.feed.empty')}</div>
    {/if}
  </div>
</Container>

<style>
  .feed-next {
    animation: feed-next 180ms ease-out;
  }

  .feed-previous {
    animation: feed-previous 180ms ease-out;
  }

  @keyframes feed-next {
    from {
      opacity: 0;
      transform: translateY(8%);
    }
  }

  @keyframes feed-previous {
    from {
      opacity: 0;
      transform: translateY(-8%);
    }
  }

  :global(.xgplayer) {
    background: #000;
  }

  :global(.xgplayer video) {
    object-fit: contain;
  }
</style>
