<script lang="ts">
  import { VideoPlayer } from '$lib/components';
  import { buildStreamUrl } from '$lib/utils';
  import { onMount, tick } from 'svelte';

  type Props = {
    path: string;
    title: string;
    uploader: string;
    startTime?: number;
    randomStart: boolean;
    muted: boolean;
    onready: () => void;
  };

  let { path, title, uploader, startTime, randomStart, muted, onready }: Props = $props();
  let player: VideoPlayer | null = null;

  export function playbackPosition(): number {
    return player?.playbackPosition() ?? 0;
  }

  onMount(() => {
    let cancelled = false;

    async function start() {
      await tick();
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (cancelled) return;
      try {
        await player?.mount({
          url: buildStreamUrl(path),
          autoplay: true,
          autoplayFallbackMuted: false,
          muted,
          startTime,
          randomStart,
          title,
          uploader
        });
      } finally {
        if (!cancelled) onready();
      }
    }

    void start();
    return () => {
      cancelled = true;
    };
  });
</script>

<VideoPlayer bind:this={player} gestureX={false} recordWatchHistory={false} showBack={false} />
