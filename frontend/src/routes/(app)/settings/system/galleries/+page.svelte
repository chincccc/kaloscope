<script lang="ts">
  import { api } from '$lib/api';
  import { Button, Dropdown, GalleryEditor, Grid, confirm } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { Gallery, Resp } from '$lib/types';
  import { debounce } from '$lib/utils';
  import { onMount, tick } from 'svelte';

  let galleries: Gallery[] = $state([]);
  let creator: GalleryEditor | null = $state(null);
  let updater: GalleryEditor | null = $state(null);
  let selected: Gallery | null = $state(null);
  const loading = createLoading();

  function getAll() {
    loading.start();
    api
      .get('gallery/lib/list')
      .json<Resp<Gallery[]>>()
      .then(({ data }) => (galleries = data))
      .finally(() => loading.end());
  }

  function scan(id: number) {
    loading.start();
    api
      .get(`gallery/lib/${id}/scan`)
      .then(() => getAll())
      .catch(() => loading.end());
  }

  function del(id: number) {
    loading.start();
    api
      .post('gallery/lib/delete', { json: { ids: [id] } })
      .then(() => getAll())
      .catch(() => loading.end());
  }

  const sort = debounce(() => {
    api.post('gallery/lib/sort', { json: { ids: galleries.map((gallery) => gallery.id) } });
  });

  onMount(getAll);
</script>

{#snippet term(name: string, description: string | null, hiddenLeft?: boolean)}
  <div class="flex items-center justify-between gap-4 py-2" title={description}>
    <dt class="whitespace-nowrap">{name}:</dt>
    <dd class="truncate opacity-70 {hiddenLeft ? 'direction-rtl' : ''}">
      {hiddenLeft && description ? description.split('').reverse().join('') : description}
    </dd>
  </div>
{/snippet}

<Grid
  data={galleries}
  loading={$loading}
  uniqueKey="id"
  class="pull-to-refresh"
  gridClass="grid-cols-sparse"
  itemClass="z-1 rounded-field border shadow-sm hover:shadow-lg"
  tailClass="rounded-field border-1 border-dashed duration-300 opacity-20 hover:opacity-50 hover:shadow-lg"
  ondragged={(data) => {
    galleries = data;
    sort();
  }}
>
  {#snippet item(gallery, index)}
    <div class="flex justify-between gap-2 rounded-t-field bg-base-200 p-4">
      <div class="grid grid-flow-col items-center gap-2">
        <iconify-icon icon={icons.imageMultiple} width="2rem" class="opacity-70"></iconify-icon>
        <div class="truncate text-base">{gallery.name}</div>
      </div>
      <div class="flex items-center gap-1">
        <Button loading={gallery.scanning} icon={icons.folderSearch} onclick={() => scan(gallery.id)} />
        <Button
          icon={icons.edit}
          onclick={() => {
            selected = gallery;
            tick().then(() => updater?.showModal());
          }}
        />
        <Dropdown contentWidth="10rem" class="dropdown-end">
          {#snippet trigger()}
            <div class="btn btn-square btn-subtle btn-sm">
              <iconify-icon icon={icons.moreVertical} width="1rem"></iconify-icon>
            </div>
          {/snippet}
          <ul class="menu gap-1">
            <li class={index === 0 ? 'menu-disabled' : ''}>
              <button
                class="px-2"
                onclick={() => {
                  [galleries[index - 1], galleries[index]] = [galleries[index], galleries[index - 1]];
                  sort();
                }}
              >
                <iconify-icon icon={icons.arrowUp} width="1rem"></iconify-icon>{$_('action.move_up')}
              </button>
            </li>
            <li class={index === galleries.length - 1 ? 'menu-disabled' : ''}>
              <button
                class="px-2"
                onclick={() => {
                  [galleries[index + 1], galleries[index]] = [galleries[index], galleries[index + 1]];
                  sort();
                }}
              >
                <iconify-icon icon={icons.arrowDown} width="1rem"></iconify-icon>{$_('action.move_down')}
              </button>
            </li>
            <li>
              <button
                class="px-2"
                onclick={() =>
                  confirm({
                    icon: icons.delete,
                    title: `${$_('action.delete', $_('entity.gallery'))} [${gallery.name}]`,
                    onconfirm: () => del(gallery.id)
                  })}
              >
                <iconify-icon icon={icons.delete} width="1rem"></iconify-icon>{$_('action.delete')}
              </button>
            </li>
          </ul>
        </Dropdown>
      </div>
    </div>
    <dl class="rounded-b-field bg-base-100 p-4 text-sm">
      {@render term($_('field.dir'), gallery.dir, true)}
      <div class="divider my-0"></div>
      {@render term($_('gallery.image_count'), String(gallery.item_count))}
    </dl>
  {/snippet}

  {#snippet tail()}
    <button class="flex-col-center size-full cursor-pointer gap-2 p-4" onclick={() => creator?.showModal()}>
      <iconify-icon icon={icons.addCircle} width="2.5rem"></iconify-icon>
      <span class="text-2xl">{$_('action.add')}</span>
    </button>
  {/snippet}
</Grid>

<GalleryEditor bind:this={creator} onsave={getAll} />
{#if selected}<GalleryEditor bind:this={updater} {...selected} onsave={getAll} />{/if}
