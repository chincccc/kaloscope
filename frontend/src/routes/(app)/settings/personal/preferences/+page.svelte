<script lang="ts">
  import { api } from '$lib/api';
  import { Container, Label, Select, Setting } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { user } from '$lib/stores';
  import type { RatingDimension, Resp } from '$lib/types';
  import { onMount } from 'svelte';

  // the loading state
  const loading = createLoading();
  let ratingDimensions = $state<RatingDimension[]>([]);
  let dimensionName = $state('');
  let dimensionSaving = $state(false);
  let customDimensionCount = $derived(ratingDimensions.filter((item) => item.removable).length);

  // the homepage options
  const homepageOptions = $derived.by(() => {
    const options = [
      { value: '/dashboard', label: 'nav.dashboard.title' },
      { value: '/feed', label: 'nav.feed.title' },
      { value: '/websearch', label: 'nav.websearch.title' },
      { value: '/medialibs', label: 'nav.medialibs.title' },
      { value: '/galleries', label: 'nav.galleries.title' },
      { value: '/settings', label: 'nav.settings.title' }
    ];
    if ($user?.role === 'admin') {
      options.splice(options.length - 1, 0, { value: '/downloads', label: 'nav.downloads.title' });
    }
    return options;
  });

  /**
   * Update the preference value.
   *
   * @param key - The preference key.
   */
  function update(key: string) {
    if ($user?.preferences) {
      api
        .post('user/update_pref', {
          json: { key: key, value: $user.preferences[key] }
        })
        .catch(() => {
          user.set(null);
        });
    }
  }

  async function loadRatingDimensions() {
    const response = await api.get('rating/dimensions').json<Resp<RatingDimension[]>>();
    ratingDimensions = response.data;
  }

  async function addRatingDimension() {
    const name = dimensionName.trim();
    if (!name || dimensionSaving) return;
    dimensionSaving = true;
    try {
      const response = await api.post('rating/dimensions', { json: { name } }).json<Resp<RatingDimension>>();
      ratingDimensions = [...ratingDimensions, response.data];
      dimensionName = '';
    } finally {
      dimensionSaving = false;
    }
  }

  async function removeRatingDimension(dimension: RatingDimension) {
    if (!dimension.removable || dimensionSaving) return;
    dimensionSaving = true;
    try {
      await api.delete(`rating/dimensions/${dimension.key}`);
      ratingDimensions = ratingDimensions.filter((item) => item.key !== dimension.key);
    } finally {
      dimensionSaving = false;
    }
  }

  onMount(() => {
    void loadRatingDimensions();
    // refresh user info when mounted
    loading.start();
    user.set(null);
    $effect(() => {
      if ($user) {
        loading.end();
      }
    });
  });
</script>

<Container type="settings" loading={$loading}>
  {#if $user?.preferences}
    <Setting title={$_('preference.navigation.title')}>
      <fieldset class="fieldset">
        <Label>{$_('preference.navigation.homepage')}</Label>
        <Select
          translate
          options={homepageOptions}
          bind:value={$user.preferences.homepage}
          onchange={() => update('homepage')}
          class="w-full"
        />
      </fieldset>
      <fieldset class="fieldset">
        <Label tip={$_('preference.navigation.vibration.tip')}>{$_('preference.navigation.vibration.title')}</Label>
        <Select
          translate
          options={[
            { value: false, label: 'action.toggle_off' },
            { value: true, label: 'action.toggle_on' }
          ]}
          bind:value={$user.preferences.vibration}
          onchange={() => update('vibration')}
          class="w-full"
        />
      </fieldset>
    </Setting>
    <Setting title={$_('preference.rating.title')} tip={$_('preference.rating.tip')}>
      <form
        class="flex gap-2"
        onsubmit={(event) => {
          event.preventDefault();
          void addRatingDimension();
        }}
      >
        <label class="input min-w-0 flex-1">
          <iconify-icon icon={icons.star} width="1.1rem" class="opacity-50"></iconify-icon>
          <input
            bind:value={dimensionName}
            maxlength="32"
            placeholder={$_('preference.rating.placeholder')}
            autocomplete="off"
          />
        </label>
        <button
          class="btn btn-square btn-primary"
          type="submit"
          disabled={!dimensionName.trim() || dimensionSaving || customDimensionCount >= 4}
          aria-label={$_('preference.rating.add')}
          title={$_('preference.rating.add')}
        >
          <iconify-icon icon={icons.addCircle} width="1.25rem"></iconify-icon>
        </button>
      </form>
      <div class="mt-2 text-right text-xs tabular-nums opacity-50">
        {customDimensionCount} / 4
      </div>
      <div class="mt-3 divide-y divide-base-300">
        {#each ratingDimensions as dimension (dimension.key)}
          <div class="flex min-h-11 items-center gap-3 py-2">
            <iconify-icon icon={dimension.removable ? icons.star : icons.lockClosed} width="1.1rem" class="opacity-55"
            ></iconify-icon>
            <span class="min-w-0 flex-1 truncate">{dimension.name}</span>
            {#if dimension.removable}
              <button
                class="btn btn-circle btn-ghost btn-sm"
                disabled={dimensionSaving}
                aria-label={$_('preference.rating.remove', { values: { name: dimension.name } })}
                title={$_('preference.rating.remove', { values: { name: dimension.name } })}
                onclick={() => void removeRatingDimension(dimension)}
              >
                <iconify-icon icon={icons.delete} width="1.1rem"></iconify-icon>
              </button>
            {:else}
              <span class="text-xs opacity-50">{$_('preference.rating.admin_only')}</span>
            {/if}
          </div>
        {/each}
      </div>
    </Setting>
    <Setting title={$_('preference.dashboard.title')} tip={$_('preference.dashboard.tip')}>
      <div>
        <fieldset class="fieldset grid-cols-2">
          <Label class="my-2">{$_('preference.dashboard.search')}</Label>
          <input
            type="checkbox"
            class="toggle self-center justify-self-end"
            bind:checked={$user.preferences.recent_searches}
            onchange={() => update('recent_searches')}
          />
        </fieldset>
        <fieldset class="fieldset grid-cols-2">
          <Label class="my-2">{$_('preference.dashboard.watch')}</Label>
          <input
            type="checkbox"
            class="toggle self-center justify-self-end"
            bind:checked={$user.preferences.recent_watches}
            onchange={() => update('recent_watches')}
          />
        </fieldset>
      </div>
    </Setting>
    <Setting title={$_('preference.privacy.title')} tip={$_('preference.privacy.tip')}>
      <fieldset class="fieldset">
        <Label>{$_('preference.privacy.search')}</Label>
        <Select
          translate
          options={[
            { value: 0, label: 'preference.privacy.untrack' },
            { value: 1, label: `1 ${$_('duration.day').toLowerCase()}` },
            { value: 3, label: `3 ${$_('duration.days').toLowerCase()}` },
            { value: 7, label: `7 ${$_('duration.days').toLowerCase()}` }
          ]}
          bind:value={$user.preferences.search_records}
          onchange={() => update('search_records')}
          class="w-full"
        />
      </fieldset>
      <fieldset class="fieldset">
        <Label>{$_('preference.privacy.watch')}</Label>
        <Select
          translate
          options={[
            { value: 0, label: 'preference.privacy.untrack' },
            { value: 1, label: `1 ${$_('duration.day').toLowerCase()}` },
            { value: 3, label: `3 ${$_('duration.days').toLowerCase()}` },
            { value: 7, label: `7 ${$_('duration.days').toLowerCase()}` }
          ]}
          bind:value={$user.preferences.watch_records}
          onchange={() => update('watch_records')}
          class="w-full"
        />
      </fieldset>
    </Setting>
  {/if}
</Container>
