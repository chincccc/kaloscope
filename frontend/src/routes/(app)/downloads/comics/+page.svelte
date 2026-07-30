<script lang="ts">
  import { tooltip } from '$lib/actions';
  import { api } from '$lib/api';
  import {
    Badge,
    Cell,
    DataView,
    DownloadDelConfirm,
    HCell,
    Image,
    Paginator,
    Search,
    Select,
    type PaginatorProps
  } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _, dateTime } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { ComicDownloadTask, Gallery, Page, Resp } from '$lib/types';
  import { onMount } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';

  let tasks: ComicDownloadTask[] = $state([]);
  let galleries: Gallery[] = $state([]);
  let keyword = $state('');
  let taskState = $state('');
  let deleteConfirm: DownloadDelConfirm;
  const loading = createLoading();
  const loadingIds = new SvelteSet<number>();
  const pagination: PaginatorProps = $state({ current: 1, size: 50, onchange: search });

  function search(page = 1, size = pagination.size, silent = false) {
    if (!silent) loading.start();
    api
      .get('download/comic/list', {
        searchParams: {
          page_num: page,
          page_size: size,
          name: keyword,
          state: taskState
        }
      })
      .json<Resp<Page<ComicDownloadTask>>>()
      .then(({ data }) => {
        tasks = data.items;
        pagination.current = page;
        pagination.size = size;
        pagination.total = data.total;
      })
      .finally(() => loading.end());
  }

  function action(task: ComicDownloadTask, name: 'pause' | 'start') {
    loadingIds.add(task.id);
    api
      .post(`download/comic/${name}`, { json: { ids: [task.id] } })
      .then(() => search(pagination.current, pagination.size, true))
      .finally(() => loadingIds.delete(task.id));
  }

  onMount(() => {
    api
      .get('gallery/lib/list')
      .json<Resp<Gallery[]>>()
      .then(({ data }) => (galleries = data));
    search();
    const timer = setInterval(() => search(pagination.current, pagination.size, true), 1000);
    return () => clearInterval(timer);
  });
</script>

<DataView dvh loading={$loading} data={tasks}>
  {#snippet filters()}
    <Select
      filter
      options={[
        { value: '', label: $_('enum.all') },
        ...(['downloading', 'paused', 'completed', 'error'] as const).map((value) => ({
          value,
          label: $_(`enum.download_state.${value}`)
        }))
      ]}
      bind:value={taskState}
      label={$_('field.status')}
      onchange={() => search()}
    />
    <Search label={$_('field.name')} bind:value={keyword} onsearch={() => search()} />
  {/snippet}

  {#snippet header()}
    <HCell width={['9rem', null]} text={$_('entity.gallery')} />
    <HCell width="100%" text={$_('field.name')} />
    <HCell width={['10rem', null]} text={$_('field.status')} />
    <HCell actions />
  {/snippet}

  {#snippet row(task)}
    <Cell class="max-lg:hidden">
      <Badge>{galleries.find((gallery) => gallery.id === task.gallery_id)?.name || '-'}</Badge>
    </Cell>
    <Cell>
      <div class="flex w-full items-center gap-3 pr-2">
        {#if task.cover}
          <Image proxy="store" src={task.cover} width="3rem" ratio="2/3" class="shrink-0" />
        {/if}
        <div class="flex min-w-0 flex-1 flex-col gap-2">
          <div class="flex items-center justify-between gap-2">
            <div class="min-w-0">
              <div class="truncate text-sm">{task.title || task.name}</div>
              {#if task.title && task.title !== task.name}
                <div class="truncate text-xs opacity-50">{task.name}</div>
              {/if}
            </div>
            {#if task.state === 'error'}
              <iconify-icon
                use:tooltip={{ content: task.error_msg || '', placement: 'left' }}
                icon={icons.info}
                width="1rem"
                class="shrink-0 text-error"
              ></iconify-icon>
            {/if}
          </div>
          <progress
            class="progress {task.state === 'downloading' ? 'progress-success' : 'opacity-50'}"
            value={task.percentage || 0}
            max="100"
          ></progress>
          <div class="flex justify-between gap-2 text-xs opacity-50">
            <span>{task.ratio}</span>
            <span>{task.state === 'completed' ? $dateTime(task.completed_at) : task.estimate}</span>
          </div>
        </div>
      </div>
    </Cell>
    <Cell class="max-lg:hidden">
      <Badge>{$_(`enum.download_state.${task.state}`)}</Badge>
    </Cell>
    <Cell
      actions={[
        {
          condition: task.state === 'downloading',
          loading: loadingIds.has(task.id),
          icon: icons.pauseFilled,
          text: $_('action.pause', $_('entity.task')),
          onclick: () => action(task, 'pause')
        },
        {
          condition: task.state === 'paused' || task.state === 'error',
          loading: loadingIds.has(task.id),
          icon: icons.playFilled,
          text: $_('action.start', $_('entity.task')),
          onclick: () => action(task, 'start')
        },
        {
          icon: icons.delete,
          text: $_('action.delete', $_('entity.task')),
          onclick: () => deleteConfirm.showModal(task.id)
        }
      ]}
    />
  {/snippet}

  {#snippet paginator(disabled)}
    <Paginator {disabled} {...pagination} />
  {/snippet}
</DataView>

<DownloadDelConfirm
  bind:this={deleteConfirm}
  endpoint="download/comic/delete"
  onconfirm={() => search(pagination.current)}
/>
