<script lang="ts" module>
  import type { Resource } from '$lib/types';

  type PlaybackLink = {
    label: string | null;
    url: string;
  };

  let modal: Modal;
  let title = $state('');
  let links: PlaybackLink[] = $state([]);

  export function getPlaybackLinks(resource: Resource): PlaybackLink[] {
    const result: PlaybackLink[] = [];
    const seen = new Set<string>();
    const append = (url: string | null | undefined, label: string | number | null = null) => {
      const value = url?.trim();
      if (!value || seen.has(value)) return;
      seen.add(value);
      result.push({ label: label === null ? null : String(label), url: value });
    };

    append(resource.url);
    for (const definition of resource.definitions ?? []) {
      append(definition.url, definition.definition);
    }
    for (const chapter of resource.chapters ?? []) {
      append(chapter.url, chapter.title);
    }
    return result;
  }

  export function hasPlaybackLinks(resource: Resource): boolean {
    return getPlaybackLinks(resource).length > 0;
  }

  export function playbackLinksPrompt(resource: Resource) {
    const available = getPlaybackLinks(resource);
    if (available.length === 0) return;
    title = resource.title || '';
    links = available;
    modal.show();
  }
</script>

<script lang="ts">
  import { Button, Modal } from '$lib/components';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { onMount } from 'svelte';

  let promptModal: Modal;

  onMount(() => {
    modal = promptModal;
  });

  function copy(url: string) {
    void navigator.clipboard?.writeText(url);
  }
</script>

<Modal
  icon={icons.info}
  title={title ? `${$_('media.playback_links')} [${title}]` : $_('media.playback_links')}
  maxWidth="44rem"
  bind:this={promptModal}
>
  <div class="flex max-h-[65vh] flex-col gap-4 overflow-y-auto py-1">
    {#each links as link, index (link.url)}
      <div class="min-w-0">
        <div class="mb-1.5 text-sm font-medium opacity-70">
          {link.label || (index === 0 ? $_('media.primary_stream') : $_('field.link'))}
        </div>
        <div class="flex items-start gap-2">
          <textarea
            class="textarea min-h-18 grow resize-y break-all font-mono text-xs"
            value={link.url}
            readonly
            aria-label={link.label || $_('media.primary_stream')}
          ></textarea>
          {#if typeof navigator !== 'undefined' && navigator.clipboard}
            <Button
              icon={icons.copy}
              title={$_('action.copy', $_('field.link'))}
              onclick={() => copy(link.url)}
            />
          {/if}
        </div>
      </div>
    {/each}
  </div>
</Modal>
