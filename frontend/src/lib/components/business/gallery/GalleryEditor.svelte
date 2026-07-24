<script lang="ts" module>
  import type { Gallery, Resp } from '$lib/types';

  type GalleryEditorProps = Partial<{
    id: number;
    dir: string;
    name: string;
    onsave: (result: Gallery) => void;
  }>;
</script>

<script lang="ts">
  import { enhance } from '$app/forms';
  import { api } from '$lib/api';
  import { FileTree, Label, Modal } from '$lib/components';
  import { createFormSchema, createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';

  let { id, dir, name, onsave }: GalleryEditorProps = $props();

  let modal: Modal;
  export const showModal = () => modal.show();

  let fileTree: FileTree;
  const loading = createLoading();
  const schema = createFormSchema(({ text }) => ({
    dir: text().maxlength(4096),
    name: text().maxlength(64)
  }));

  function upsert(form: HTMLFormElement, data: FormData) {
    loading.start();
    const json: Record<string, unknown> = Object.fromEntries(data);
    json.id = id;
    api
      .post('gallery/lib/upsert', { json })
      .json<Resp<Gallery>>()
      .then(async ({ data }) => {
        if (!id) {
          await api.get(`gallery/lib/${data.id}/scan`);
        }
        modal.close();
        onsave?.(data);
        setTimeout(() => form.reset(), 200);
      })
      .finally(() => loading.end());
  }
</script>

<Modal icon={icons.imageMultiple} title={$_(id ? 'action.edit' : 'action.add', $_('entity.gallery'))} bind:this={modal}>
  <form
    method="post"
    use:enhance={({ formElement, formData, cancel }) => {
      cancel();
      upsert(formElement, formData);
    }}
  >
    <fieldset class="fieldset">
      <Label required>{$_('field.name')}</Label>
      <input placeholder={$_('field.name')} class="input w-full truncate" bind:value={name} {...schema.name} />

      <Label required>{$_('field.dir')}</Label>
      <button
        type="button"
        aria-label={$_('action.select', $_('field.dir'))}
        class="input w-full {id ? 'cursor-not-allowed' : 'cursor-pointer'}"
        onclick={() => fileTree.showModal()}
        disabled={!!id}
      >
        <iconify-icon icon={icons.folder} width="1.5rem" class="opacity-50"></iconify-icon>
        <input
          type="text"
          autocomplete="off"
          placeholder={$_('action.select', $_('field.dir'))}
          class="grow truncate text-left direction-rtl {id ? 'cursor-not-allowed' : 'cursor-pointer'}"
          value={dir?.split('').reverse().join('')}
          disabled={!!id}
          readonly
        />
        <input type="text" class="hidden" name="dir" value={dir} {...schema.dir} />
      </button>
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>{$_('message.cancel')}</button>
      <button type="submit" class="btn btn-submit" disabled={$loading !== null}>
        {$_('message.confirm')}
        {#if $loading}<span class="loading loading-xs loading-dots"></span>{/if}
      </button>
    </div>
  </form>
</Modal>

<FileTree bind:this={fileTree} onlyDirs={true} onconfirm={(stats) => (dir = stats.path)} />
