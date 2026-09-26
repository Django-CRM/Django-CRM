<script>
  import MergeCompare from '$lib/v2/components/MergeCompare.svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();
</script>

<svelte:head>
  <title>Merge {data.singular}s</title>
</svelte:head>

<MergeCompare {data} {form} />
