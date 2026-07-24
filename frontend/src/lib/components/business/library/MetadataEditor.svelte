<script lang="ts" module>
  import type { MediaItem } from '$lib/types';

  export type MetadataEditorProps = {
    item: MediaItem;
    onsave?: () => void;
  };

  type FrameOption = {
    position: number;
    url: string;
  };
</script>

<script lang="ts">
  import { api } from '$lib/api';
  import { Image, Label, Modal } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { Resp } from '$lib/types';

  let { item: source, onsave }: MetadataEditorProps = $props();
  let modal: Modal;
  let fileInput: HTMLInputElement;
  let item: MediaItem | null = $state(null);
  let plot = $state('');
  let poster = $state('');
  let thumbnail: File | null = null;
  let preview = $state<string | null>(null);
  let selectedFrame = $state<number | null>(null);
  let frameOptions = $state<FrameOption[]>([]);
  let loadingFrames = $state(false);
  const saving = createLoading();

  let isEpisode = $derived(source.episode !== null);
  let episodePosters = $derived(
    item?.lib?.lib_type === 'tv_show' && !isEpisode ? (item.children ?? []).filter((child) => child.poster) : []
  );
  let canExtractFrames = $derived(Boolean(item && (!item.children || item.children.length === 0)));
  let selectedFrameUrl = $derived(frameOptions.find((option) => option.position === selectedFrame)?.url ?? null);
  let displayPoster = $derived(preview ?? selectedFrameUrl ?? poster);
  let posterRatio = $derived(isEpisode ? '16/9' : '2/3');

  function clearPreview() {
    if (preview) URL.revokeObjectURL(preview);
    preview = null;
  }

  function clearFrameOptions() {
    for (const option of frameOptions) URL.revokeObjectURL(option.url);
    frameOptions = [];
    selectedFrame = null;
  }

  function resetImageSelection() {
    clearPreview();
    thumbnail = null;
    selectedFrame = null;
    if (fileInput) fileInput.value = '';
  }

  function selectThumbnail(files: FileList | null) {
    resetImageSelection();
    thumbnail = files?.[0] ?? null;
    poster = '';
    if (thumbnail) preview = URL.createObjectURL(thumbnail);
  }

  function selectPoster(value: string) {
    resetImageSelection();
    poster = value;
  }

  function selectFrame(position: number) {
    resetImageSelection();
    poster = '';
    selectedFrame = position;
  }

  async function loadFrames() {
    if (!item || loadingFrames) return;
    clearFrameOptions();
    loadingFrames = true;
    const loaded: FrameOption[] = [];
    try {
      const response = await api.get(`media/${item.id}/frames`).json<Resp<{ positions: number[] }>>();
      for (const position of response.data.positions) {
        const blob = await api.get(`media/${item.id}/frame`, { searchParams: { position: String(position) } }).blob();
        loaded.push({ position, url: URL.createObjectURL(blob) });
      }
      frameOptions = loaded;
    } catch (error) {
      for (const option of loaded) URL.revokeObjectURL(option.url);
      throw error;
    } finally {
      loadingFrames = false;
    }
  }

  export async function showModal() {
    item = (await api.get(`media/${source.id}`).json<Resp<MediaItem>>()).data;
    plot = item.metadata?.plot ?? '';
    poster = item.poster ?? '';
    thumbnail = null;
    clearPreview();
    clearFrameOptions();
    modal.show();
  }

  function closeEditor() {
    modal.close();
    clearPreview();
    clearFrameOptions();
  }

  function save() {
    if (!item) return;
    const form = new FormData();
    form.set('plot', plot);
    form.set('poster', poster);
    if (thumbnail) form.set('thumbnail', thumbnail);
    if (selectedFrame !== null) form.set('frame', String(selectedFrame));
    saving.start();
    api
      .post(`media/${item.id}/metadata`, { body: form })
      .then(() => {
        closeEditor();
        onsave?.();
      })
      .finally(() => saving.end());
  }
</script>

<Modal icon={icons.edit} title={$_('action.edit', $_('entity.metadata'))} maxWidth="52rem" bind:this={modal}>
  <div class="grid gap-4 sm:grid-cols-[10rem_minmax(0,1fr)]">
    <div class="flex flex-col items-center gap-2">
      <div class="group relative">
        <Image
          proxy="store"
          src={displayPoster}
          text={item?.title ?? item?.name}
          width="10rem"
          ratio={posterRatio}
          border
        />
        {#if displayPoster}
          <button
            class="btn absolute top-1 right-1 btn-square opacity-0 btn-sm group-hover:opacity-100"
            aria-label="Delete"
            onclick={() => {
              resetImageSelection();
              poster = '';
            }}
          >
            <iconify-icon icon={icons.delete} width="1.25rem"></iconify-icon>
          </button>
        {/if}
      </div>
      <input
        bind:this={fileInput}
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp"
        class="file-input w-full file-input-sm"
        onchange={(event) => selectThumbnail(event.currentTarget.files)}
      />
    </div>

    <fieldset class="fieldset min-w-0">
      <Label>{$_('field.thumbnail')}</Label>
      <input
        class="input w-full"
        placeholder={$_('field.link')}
        bind:value={poster}
        oninput={() => resetImageSelection()}
      />
      <Label>{$_('field.plot')}</Label>
      <textarea class="textarea min-h-40 w-full" maxlength="50000" bind:value={plot}></textarea>
    </fieldset>

    {#if episodePosters.length}
      <fieldset class="fieldset min-w-0 sm:col-span-2">
        <Label>{$_('media.episode_covers')}</Label>
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {#each episodePosters as episode (episode.id)}
            <button
              class="relative overflow-hidden rounded-sm border-2 transition-colors"
              class:border-primary={poster === episode.poster}
              class:border-transparent={poster !== episode.poster}
              title={episode.title ?? episode.name}
              onclick={() => selectPoster(episode.poster!)}
            >
              <Image
                proxy="store"
                src={episode.poster}
                text={episode.title ?? episode.name}
                width="100%"
                ratio="16/9"
              />
              {#if poster === episode.poster}
                <span
                  class="absolute right-1 bottom-1 flex size-6 items-center justify-center rounded-full bg-primary text-primary-content"
                >
                  <iconify-icon icon={icons.check} width="1rem"></iconify-icon>
                </span>
              {/if}
            </button>
          {/each}
        </div>
      </fieldset>
    {/if}

    {#if canExtractFrames}
      <fieldset class="fieldset min-w-0 sm:col-span-2">
        <div class="flex items-center justify-between gap-3">
          <Label>{$_('media.video_frames')}</Label>
          <button class="btn btn-sm" disabled={loadingFrames} onclick={loadFrames}>
            <iconify-icon icon={icons.image} width="1rem"></iconify-icon>
            {$_('media.extract_frames')}
            {#if loadingFrames}<span class="loading loading-xs loading-dots"></span>{/if}
          </button>
        </div>
        {#if frameOptions.length}
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {#each frameOptions as option (option.position)}
              <button
                class="relative overflow-hidden rounded-sm border-2 transition-colors"
                class:border-primary={selectedFrame === option.position}
                class:border-transparent={selectedFrame !== option.position}
                onclick={() => selectFrame(option.position)}
              >
                <Image src={option.url} width="100%" ratio="16/9" />
                {#if selectedFrame === option.position}
                  <span
                    class="absolute right-1 bottom-1 flex size-6 items-center justify-center rounded-full bg-primary text-primary-content"
                  >
                    <iconify-icon icon={icons.check} width="1rem"></iconify-icon>
                  </span>
                {/if}
              </button>
            {/each}
          </div>
        {/if}
      </fieldset>
    {/if}
  </div>

  <div class="modal-action">
    <button class="btn" onclick={closeEditor}>{$_('message.cancel')}</button>
    <button class="btn btn-submit" disabled={$saving !== null} onclick={save}>
      {$_('message.confirm')}
      {#if $saving}<span class="loading loading-xs loading-dots"></span>{/if}
    </button>
  </div>
</Modal>
