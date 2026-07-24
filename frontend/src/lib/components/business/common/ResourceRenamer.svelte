<script lang="ts">
  import { api } from '$lib/api';
  import { Label, Modal } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';

  let { onsave }: { onsave?: () => void } = $props();
  let modal: Modal;
  let endpoint = '';
  let name = $state('');
  const saving = createLoading();

  export function showModal(target: { endpoint: string; name: string }) {
    endpoint = target.endpoint;
    name = target.name;
    modal.show();
  }

  function save() {
    const value = name.trim();
    if (!endpoint || !value) return;
    saving.start();
    api
      .post(endpoint, { json: { name: value } })
      .then(() => {
        modal.close();
        onsave?.();
      })
      .finally(() => saving.end());
  }
</script>

<Modal icon={icons.edit} title={$_('action.rename')} bind:this={modal}>
  <form
    onsubmit={(event) => {
      event.preventDefault();
      save();
    }}
  >
    <fieldset class="fieldset">
      <Label>{$_('field.name')}</Label>
      <input class="input w-full" maxlength="255" autocomplete="off" bind:value={name} />
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>{$_('message.cancel')}</button>
      <button type="submit" class="btn btn-submit" disabled={$saving !== null || !name.trim()}>
        {$_('message.confirm')}
        {#if $saving}<span class="loading loading-xs loading-dots"></span>{/if}
      </button>
    </div>
  </form>
</Modal>
