<script lang="ts">
  import { api } from '$lib/api';
  import { Container, Label, Setting } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { user } from '$lib/stores';
  import { onMount } from 'svelte';

  const loading = createLoading();

  function update(key: string) {
    if (!$user?.preferences) return;
    api
      .post('user/update_pref', {
        json: { key, value: $user.preferences[key] }
      })
      .catch(() => user.set(null));
  }

  onMount(() => {
    loading.start();
    user.set(null);
    $effect(() => {
      if ($user) loading.end();
    });
  });
</script>

<Container type="settings" loading={$loading}>
  {#if $user?.preferences}
    <Setting title={$_('preference.feed.title')}>
      <fieldset class="fieldset grid-cols-2">
        <Label class="my-2 justify-start" tipPlacement="right" tip={$_('preference.feed.random_start.tip')}>
          {$_('preference.feed.random_start.title')}
        </Label>
        <input
          type="checkbox"
          class="toggle self-center justify-self-end"
          bind:checked={$user.preferences.feed_random_start}
          onchange={() => update('feed_random_start')}
        />
      </fieldset>
    </Setting>
  {/if}
</Container>
