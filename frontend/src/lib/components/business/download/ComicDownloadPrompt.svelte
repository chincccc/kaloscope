<script lang="ts" module>
  import { api } from '$lib/api';
  import { createLoading } from '$lib/helpers';
  import type { Gallery, Resource, Resp } from '$lib/types';

  let modal: Modal;
  let galleries: Gallery[] = $state([]);
  let galleryId = $state(0);
  let title = $state('');
  let filename = $state('');
  let url = $state('');
  let headers: Record<string, string> = $state({});
  const loading = createLoading();

  export function hasComicDownload(resource: Resource): boolean {
    const direct = resource.download?.url;
    return !!direct || (resource.media_type === 'image' && /\.(?:cbz|zip)(?:$|[?#])/i.test(resource.link || ''));
  }

  export async function comicDownloadPrompt(resource: Resource) {
    if (!hasComicDownload(resource)) return;
    const response = await api.get('gallery/lib/list').json<Resp<Gallery[]>>();
    galleries = response.data;
    if (!galleries.length) return;
    const metadata = resource.download;
    title = resource.title || metadata?.filename || 'comic';
    filename = metadata?.filename || '';
    url = metadata?.url || resource.link || '';
    headers = metadata?.headers || {};
    galleryId = galleries.some((gallery) => gallery.id === metadata?.gallery_id)
      ? metadata?.gallery_id || galleries[0].id
      : galleries[0].id;
    modal.show();
  }

  function submit() {
    loading.start();
    api
      .post('download/comic/add', {
        json: {
          url,
          title,
          filename: filename.trim() || null,
          headers,
          gallery_id: galleryId
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

<Modal icon={icons.download} title={$_('download.comic.title')} maxWidth="32rem" bind:this={modal}>
  <form
    onsubmit={(event) => {
      event.preventDefault();
      submit();
    }}
  >
    <fieldset class="fieldset">
      <Label required>{$_('entity.gallery')}</Label>
      <Select
        required
        options={galleries.map((gallery) => ({ value: gallery.id, label: gallery.name }))}
        bind:value={galleryId}
        class="w-full"
      />
      <Label>{$_('field.name')}</Label>
      <input class="input w-full" maxlength="255" placeholder={title} bind:value={filename} />
      <Label>{$_('field.link')}</Label>
      <input class="input w-full" value={url} readonly />
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>{$_('message.cancel')}</button>
      <button type="submit" class="btn btn-submit" disabled={$loading !== null || !galleryId}>
        {$_('action.download', '')}
        {#if $loading}<span class="loading loading-xs loading-dots"></span>{/if}
      </button>
    </div>
  </form>
</Modal>
