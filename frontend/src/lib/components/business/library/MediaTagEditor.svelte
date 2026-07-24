<script lang="ts" module>
  export type MediaTagEditorProps = {
    onsave?: () => void;
  };
  export type MediaTagTarget = { endpoint: string; tags?: string[] };
</script>

<script lang="ts">
  import { api } from '$lib/api';
  import { Label, Modal } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';

  let { onsave }: MediaTagEditorProps = $props();
  let modal: Modal;
  let endpoint = '';
  let input = $state('');
  let tags: string[] = $state([]);
  const saving = createLoading();

  export function showModal(target: MediaTagTarget) {
    endpoint = target.endpoint;
    tags = [...(target.tags ?? [])];
    input = '';
    modal.show();
  }

  function addInput() {
    const known = new Set(tags.map((tag) => tag.toLocaleLowerCase()));
    for (const tag of input.split(/[\s_#]+/u).map((value) => value.trim())) {
      if (tag && !known.has(tag.toLocaleLowerCase())) {
        tags.push(tag);
        known.add(tag.toLocaleLowerCase());
      }
    }
    input = '';
  }

  function save() {
    addInput();
    if (!endpoint) return;
    saving.start();
    api
      .post(endpoint, { json: { tags } })
      .then(() => {
        modal.close();
        onsave?.();
      })
      .finally(() => saving.end());
  }
</script>

<Modal icon={icons.edit} title={$_('media.edit_tags')} bind:this={modal}>
  <form
    onsubmit={(event) => {
      event.preventDefault();
      save();
    }}
  >
    <fieldset class="fieldset">
      <Label>{$_('media.tags')}</Label>
      <div class="flex gap-2">
        <label class="input min-w-0 flex-1">
          <span class="opacity-50">#</span>
          <input
            maxlength="64"
            autocomplete="off"
            placeholder={$_('media.tag_placeholder')}
            bind:value={input}
            onkeydown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                addInput();
              }
            }}
          />
        </label>
        <button type="button" class="btn btn-square" aria-label={$_('media.add_tag')} onclick={addInput}>
          <iconify-icon icon={icons.addCircle} width="1.25rem"></iconify-icon>
        </button>
      </div>
      {#if tags.length}
        <div class="mt-2 flex min-h-8 flex-wrap gap-2">
          {#each tags as tag, index (`${tag}-${index}`)}
            <span class="badge h-8 gap-1 badge-soft badge-primary">
              #{tag}
              <button
                type="button"
                class="flex size-5 items-center justify-center rounded-full hover:bg-base-content/15"
                aria-label={$_('media.remove_tag', tag)}
                onclick={() => tags.splice(index, 1)}
              >
                <iconify-icon icon={icons.clear} width="0.875rem"></iconify-icon>
              </button>
            </span>
          {/each}
        </div>
      {/if}
      <p class="mt-1 text-xs opacity-50">{$_('media.tag_tip')}</p>
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>{$_('message.cancel')}</button>
      <button type="submit" class="btn btn-submit" disabled={$saving !== null}>
        {$_('message.confirm')}
        {#if $saving}<span class="loading loading-xs loading-dots"></span>{/if}
      </button>
    </div>
  </form>
</Modal>
