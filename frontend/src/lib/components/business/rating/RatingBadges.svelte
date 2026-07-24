<script lang="ts">
  import type { RatingValue } from '$lib/types';

  let {
    values = [],
    class: className = ''
  }: {
    values?: RatingValue[];
    class?: string;
  } = $props();
</script>

{#if values.length}
  <div class="flex max-w-full flex-col items-start gap-1 {className}">
    {#each values as value (value.key)}
      <span
        class="flex max-w-full items-center gap-1 rounded-sm bg-black/75 px-1.5 py-0.5 text-[0.6875rem] leading-tight text-white shadow-sm backdrop-blur-sm"
        title={`${value.name}: ${value.score}/10`}
      >
        <iconify-icon icon="fluent:star-12-filled" width="0.7rem" class="shrink-0 text-warning"></iconify-icon>
        <span class="max-w-20 truncate">{value.name}</span>
        <strong class="shrink-0 tabular-nums">{value.score}</strong>
      </span>
    {/each}
  </div>
{/if}
