<script lang="ts">
  import { page } from '$app/state';
  import { api } from '$lib/api';
  import {
    ImageViewer,
    Overlay,
    TextViewer,
    VideoPlayer,
    comicDownloadPrompt,
    hasComicDownload
  } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { user } from '$lib/stores';
  import type { Chapter, Resource, Resp } from '$lib/types';
  import { isDashSupported } from '$lib/utils';
  import { onMount, tick } from 'svelte';
  import { queryParameters, ssp } from 'sveltekit-search-params';
  import { UAParser } from 'ua-parser-js';

  // the URL query parameters
  const query = queryParameters(
    {
      chapter_id: ssp.string(),
      resource_id: ssp.string(),
      media_type: ssp.string(),
      video_type: ssp.string()
    },
    {
      pushHistory: false
    }
  );

  // the websearch result
  let resource: Resource | null = $state(null);
  let mediaType: string | null = $derived.by(() => resource?.media_type ?? query.media_type);
  let videoType: string | null = $derived.by(() => resource?.video_type ?? query.video_type);

  let refreshKey: number = $state(0);
  // the text viewer instance
  let textViewer: TextViewer | null = $state(null);
  // the image viewer instance
  let imageViewer: ImageViewer | null = $state(null);
  // the video player instance
  let videoPlayer: VideoPlayer | null = $state(null);
  let sourcePage: string = $derived(query.resource_id ?? page.params.rsrc_id);

  // the loading state
  const loading = createLoading();

  // the active chapter ID
  let activeChapterId: string | null = $state(null);
  // guard to limit fallback to first chapter once per load
  let chapterFallbackTried: boolean = false;

  /**
   * Fetch the details of the resource.
   *
   * @param chapterId - The chapter ID to request.
   */
  function details(chapterId: string | null = null) {
    // reset fallback guard on initial loads
    if (chapterId === null) {
      chapterFallbackTried = false;
    }
    activeChapterId = chapterId ?? query.chapter_id ?? null;

    const userAgent = UAParser(navigator.userAgent);
    loading.start();
    api
      .post(`flow/graph/${page.params.indexer_id}/execute`, {
        json: {
          $start: 'details_start',
          id: query.resource_id ?? page.params.rsrc_id,
          chapter_id: activeChapterId,
          dash_supported: isDashSupported(),
          ua: {
            ...userAgent,
            navigator: {
              platform: navigator.platform,
              maxTouchPoints: navigator.maxTouchPoints
            }
          }
        }
      })
      .json<Resp<Resource | null>>()
      .then(({ data }) => {
        if (chapterId !== null) {
          refreshKey = new Date().getTime();
        }
        tick().then(() => onload(data));
      })
      .finally(() => {
        loading.end();
      });
  }

  async function loadMoreImages(pageNumber: number) {
    const resp = await api
      .post(`flow/graph/${page.params.indexer_id}/execute`, {
        json: {
          $start: 'details_start',
          id: query.resource_id ?? page.params.rsrc_id,
          chapter_id: query.chapter_id ?? activeChapterId,
          page: pageNumber
        }
      })
      .json<Resp<Resource | null>>();
    const data = resp.data;
    return data
      ? {
          images: data.images,
          image_count: data.image_count
        }
      : null;
  }

  /**
   * Handle the resource load event.
   *
   * @param rsrc - The resource object.
   */
  function onload(rsrc: Resource | null) {
    if (!rsrc) {
      return;
    }
    resource = rsrc;
    const chapters = rsrc.chapters ?? [];
    activeChapterId ??= chapters[0]?.id ?? null;
    // load the viewer/player based on the media type
    if (mediaType === 'text' && textViewer) {
      if (rsrc.text === null || rsrc.text === undefined) {
        fallbackToFirst(chapters);
      } else {
        textViewer.mount({
          text: rsrc.text,
          title: rsrc.title,
          chapters: chapters,
          chapterId: activeChapterId,
          chapterChange: onchange
        });
      }
    } else if (mediaType === 'image' && imageViewer) {
      if (rsrc.images === null || rsrc.images === undefined) {
        fallbackToFirst(chapters);
      } else {
        imageViewer.mount({
          images: rsrc.images,
          image_count: rsrc.image_count,
          title: rsrc.title,
          chapters: chapters,
          chapterId: activeChapterId,
          chapterChange: onchange,
          loadMore: loadMoreImages
        });
      }
    } else if (mediaType === 'video' && videoPlayer) {
      if (rsrc.url === null || rsrc.url === undefined) {
        fallbackToFirst(chapters);
      } else {
        videoPlayer.mount({
          url: rsrc.url,
          title: rsrc.title,
          danmakus: rsrc.danmakus,
          videoType: videoType,
          proxy: true,
          referer: sourcePage,
          chapters: chapters,
          chapterId: activeChapterId,
          chapterChange: onchange,
          definitions: rsrc.definitions,
          uploader: rsrc.uploader,
          uploadedAt: rsrc.uploaded_at
        });
      }
    }
  }

  /**
   * Fall back to the first chapter when the resource has no primary content.
   *
   * @param chapters - The chapter array.
   */
  function fallbackToFirst(chapters: Chapter[]) {
    if (chapters.length > 0 && !chapterFallbackTried) {
      chapterFallbackTried = true;
      onchange(chapters[0]);
    }
  }

  /**
   * Handle the chapter change event.
   *
   * @param chapter - The chapter object.
   */
  function onchange(chapter: Chapter) {
    const { id, url, title } = chapter;
    // change the video chapter directly if the URL is available
    if (mediaType === 'video' && videoPlayer && url) {
      videoPlayer.mount({
        next: true,
        url,
        title,
        videoType: videoType ?? undefined,
        proxy: true,
        referer: sourcePage
      });
      return;
    }
    // update the query parameter and request the new chapter
    if (id && id !== query.chapter_id) {
      query.chapter_id = id;
      details(id);
    }
  }

  onMount(() => {
    details();
  });
</script>

<div class="history-back fixed inset-0 layer-1 {mediaType === 'video' ? 'max-sm:bottom-(--ks-dock-h)' : ''}">
  <Overlay black={mediaType !== 'text'} loading={$loading} />
  {#key refreshKey}
    {#if mediaType === 'text'}
      <TextViewer bind:this={textViewer} />
    {:else if mediaType === 'image'}
      <ImageViewer bind:this={imageViewer} />
    {:else if mediaType === 'video'}
      <VideoPlayer bind:this={videoPlayer} />
    {/if}
  {/key}
  {#if resource && hasComicDownload(resource) && $user?.role === 'admin'}
    <button
      class="btn fixed top-1.5 right-12 z-4 border-0 bg-black/50 btn-ghost text-white/80 shadow-none btn-xs"
      aria-label={$_('download.comic.title')}
      title={$_('download.comic.title')}
      onclick={() => resource && comicDownloadPrompt(resource)}
    >
      <iconify-icon icon={icons.download} width="1.25rem"></iconify-icon>
    </button>
  {/if}
</div>
