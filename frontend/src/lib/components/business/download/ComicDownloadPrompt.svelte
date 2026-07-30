<script lang="ts" module>
  import { api } from '$lib/api';
  import { createLoading } from '$lib/helpers';
  import type { Gallery, MediaLib, Resource, Resp } from '$lib/types';

  type BuiltinDownloadType = 'comic' | 'video' | 'hls';

  let modal: Modal;
  let galleries: Gallery[] = $state([]);
  let mediaLibs: MediaLib[] = $state([]);
  let galleryId = $state(0);
  let mediaLibId = $state(0);
  let downloadType: BuiltinDownloadType = $state('comic');
  let title = $state('');
  let filename = $state('');
  let url = $state('');
  let resourceCover = $state<string | null>(null);
  let headers: Record<string, string> = $state({});
  const loading = createLoading();

  export function hasComicDownload(resource: Resource): boolean {
    const type = resource.download?.type;
    const archive = resource.download?.filename || resource.download?.url || resource.link || '';
    return (
      (type === 'comic' && !!resource.download?.url) ||
      (!type && /\.(?:cbz|zip)(?:$|[?#])/i.test(archive)) ||
      (!type && resource.media_type === 'image' && !!resource.download?.url)
    );
  }

  export function hasBuiltinDownload(resource: Resource): boolean {
    return !!resource.download?.url || hasComicDownload(resource);
  }

  function resolveType(resource: Resource): BuiltinDownloadType {
    const configured = resource.download?.type;
    if (configured === 'comic' || configured === 'video' || configured === 'hls') return configured;
    if (hasComicDownload(resource)) return 'comic';
    return resource.video_type === 'hls' || /\.m3u8(?:$|[?#])/i.test(resource.download?.url || '') ? 'hls' : 'video';
  }

  export async function builtinDownloadPrompt(resource: Resource) {
    if (!hasBuiltinDownload(resource)) return;
    downloadType = resolveType(resource);
    if (downloadType === 'comic') {
      const response = await api.get('gallery/lib/list').json<Resp<Gallery[]>>();
      galleries = response.data;
      if (!galleries.length) return;
    } else {
      const response = await api.get('media/lib/list').json<Resp<MediaLib[]>>();
      mediaLibs = response.data;
      if (!mediaLibs.length) return;
    }
    const metadata = resource.download;
    title = resource.title || metadata?.filename || (downloadType === 'comic' ? 'comic' : 'video');
    // Comic source names are often opaque IDs, while video workflows commonly
    // provide a deliberate filename with the desired container suffix.
    filename = downloadType === 'comic' ? '' : metadata?.filename || '';
    url = metadata?.url || resource.link || '';
    resourceCover = resource.cover || null;
    headers = metadata?.headers || {};
    if (downloadType === 'comic') {
      galleryId = galleries.some((gallery) => gallery.id === metadata?.gallery_id)
        ? metadata?.gallery_id || galleries[0].id
        : galleries[0].id;
    } else {
      mediaLibId = mediaLibs.some((lib) => lib.id === metadata?.media_lib_id)
        ? metadata?.media_lib_id || mediaLibs[0].id
        : mediaLibs[0].id;
    }
    modal.show();
  }

  export async function comicDownloadPrompt(resource: Resource) {
    return builtinDownloadPrompt(resource);
  }

  function submit() {
    loading.start();
    api
      .post('download/builtin/add', {
        json: {
          type: downloadType,
          url,
          title,
          cover: resourceCover,
          filename: filename.trim() || null,
          headers,
          gallery_id: downloadType === 'comic' ? galleryId : null,
          media_lib_id: downloadType === 'comic' ? null : mediaLibId
        }
      })
      .then(() => modal.close())
      .finally(() => loading.end());
  }
</script>

<script lang="ts">
  import { Label, Modal, Select } from '$lib/components';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
</script>

<Modal
  icon={icons.download}
  title={$_(downloadType === 'comic' ? 'download.comic.title' : 'download.video.title')}
  maxWidth="32rem"
  bind:this={modal}
>
  <form
    onsubmit={(event) => {
      event.preventDefault();
      submit();
    }}
  >
    <fieldset class="fieldset">
      {#if downloadType === 'comic'}
        <Label required>{$_('entity.gallery')}</Label>
        <Select
          required
          options={galleries.map((gallery) => ({ value: gallery.id, label: gallery.name }))}
          bind:value={galleryId}
          class="w-full"
        />
      {:else}
        <Label required>{$_('entity.media_lib')}</Label>
        <Select
          required
          options={mediaLibs.map((lib) => ({ value: lib.id, label: lib.name }))}
          bind:value={mediaLibId}
          class="w-full"
        />
      {/if}
      <Label>{$_('field.name')}</Label>
      <input class="input w-full" maxlength="255" placeholder={title} bind:value={filename} />
      <Label>{$_('field.link')}</Label>
      <input class="input w-full" value={url} readonly />
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>{$_('message.cancel')}</button>
      <button
        type="submit"
        class="btn btn-submit"
        disabled={$loading !== null || (downloadType === 'comic' ? !galleryId : !mediaLibId)}
      >
        {$_('action.download', '')}
        {#if $loading}<span class="loading loading-xs loading-dots"></span>{/if}
      </button>
    </div>
  </form>
</Modal>
