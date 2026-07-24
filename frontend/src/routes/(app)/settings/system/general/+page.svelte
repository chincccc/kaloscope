<script lang="ts">
  import { api } from '$lib/api';
  import { Container, Label, Select, Setting } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import type { GlobalConfig, Page, Resp } from '$lib/types';
  import { onMount } from 'svelte';

  // hardware acceleration options supported by the backend
  const hwaccelOptions = [
    { value: null, label: 'transcode.hwaccel.none' },
    { value: 'qsv', label: 'Intel QuickSync (QSV)' },
    { value: 'vaapi', label: 'Video Acceleration API (VAAPI)' },
    { value: 'nvenc', label: 'Nvidia NVENC' },
    { value: 'videotoolbox', label: 'Apple VideoToolBox' }
  ];

  // quality options
  const qualityOptions = [
    { value: 'low', label: 'transcode.quality.low' },
    { value: 'medium', label: 'transcode.quality.medium' },
    { value: 'high', label: 'transcode.quality.high' }
  ];

  const thumbnailSourceOptions = [
    { value: 'first', label: 'media.thumbnail_source_options.first' },
    { value: 'middle', label: 'media.thumbnail_source_options.middle' },
    { value: 'random', label: 'media.thumbnail_source_options.random' }
  ];

  // the loading state
  const loading = createLoading();

  // the config values, initialized with defaults
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let configs: Record<string, any> = $state({
    'ffmpeg.path': '',
    'vaapi.device': '',
    'transcode.enabled': false,
    'transcode.hwaccel': null,
    'transcode.quality': 'medium',
    'media.thumbnail_source': 'first',
    'media.screenshot_count': 6
  });

  /**
   * Persist a config value to the backend.
   *
   * @param key - The config key.
   */
  function setValue(key: string) {
    api.post('config/upsert', {
      json: { key: key, value: configs[key] }
    });
  }
  function setScreenshotCount() {
    configs['media.screenshot_count'] = Math.max(
      0,
      Math.min(24, Math.round(Number(configs['media.screenshot_count']) || 0))
    );
    setValue('media.screenshot_count');
  }

  /**
   * Load all configs from the backend.
   */
  function loadAll() {
    loading.start();
    api
      .get('config/list', { searchParams: { page_num: 0 } })
      .json<Resp<Page<GlobalConfig>>>()
      .then(({ data }) => {
        for (const cfg of data.items) {
          configs[cfg.key] = cfg.value;
        }
      })
      .finally(() => loading.end());
  }

  onMount(() => {
    loadAll();
  });
</script>

<Container type="settings" loading={$loading}>
  <Setting title={$_('media.thumbnails')}>
    <fieldset class="fieldset">
      <Label tip={$_('media.thumbnail_source_tip')}>{$_('media.thumbnail_source')}</Label>
      <Select
        translate
        options={thumbnailSourceOptions}
        bind:value={configs['media.thumbnail_source']}
        onchange={() => setValue('media.thumbnail_source')}
        class="w-full"
      />
    </fieldset>
  </Setting>

  <Setting title={$_('media.screenshots')}>
    <fieldset class="fieldset">
      <Label tip={$_('media.screenshot_count_tip')}>{$_('media.screenshot_count')}</Label>
      <div class="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-3">
        <input
          type="range"
          class="range w-full range-primary range-sm"
          min="0"
          max="24"
          step="1"
          bind:value={configs['media.screenshot_count']}
          onchange={setScreenshotCount}
        />
        <input
          type="number"
          class="input w-full input-sm"
          min="0"
          max="24"
          step="1"
          bind:value={configs['media.screenshot_count']}
          onchange={setScreenshotCount}
        />
      </div>
    </fieldset>
  </Setting>

  <Setting title={$_('ffmpeg.title')}>
    <fieldset class="fieldset">
      <Label tip={$_('ffmpeg.path.tip')}>
        {$_('ffmpeg.path.title')}
      </Label>
      <input
        type="text"
        class="input w-full"
        placeholder="/usr/local/bin/ffmpeg"
        bind:value={configs['ffmpeg.path']}
        onchange={() => setValue('ffmpeg.path')}
      />
    </fieldset>
  </Setting>

  <Setting title={$_('transcode.title')}>
    <fieldset class="fieldset grid-cols-2">
      <Label class="my-2 justify-start" tipPlacement="right" tip={$_('transcode.auto.tip')}>
        {$_('transcode.auto.title')}
      </Label>
      <input
        type="checkbox"
        class="toggle self-center justify-self-end"
        bind:checked={configs['transcode.auto']}
        onchange={() => setValue('transcode.auto')}
      />
    </fieldset>
    <fieldset class="fieldset">
      <Label tip={$_('transcode.quality.tip')}>
        {$_('transcode.quality.title')}
      </Label>
      <Select
        translate
        options={qualityOptions}
        bind:value={configs['transcode.quality']}
        onchange={() => setValue('transcode.quality')}
        class="w-full"
      />
    </fieldset>
    <fieldset class="fieldset">
      <Label tip={$_('transcode.hwaccel.tip')}>
        {$_('transcode.hwaccel.title')}
      </Label>
      <Select
        translate
        options={hwaccelOptions}
        bind:value={configs['transcode.hwaccel']}
        onchange={() => setValue('transcode.hwaccel')}
        class="w-full"
      />
    </fieldset>
    <fieldset class="fieldset">
      <Label tip={$_('transcode.vaapi_device.tip')}>
        {$_('transcode.vaapi_device.title')}
      </Label>
      <input
        type="text"
        class="input w-full"
        placeholder="/dev/dri/renderD128"
        bind:value={configs['vaapi.device']}
        onchange={() => setValue('vaapi.device')}
      />
    </fieldset>
  </Setting>
</Container>
