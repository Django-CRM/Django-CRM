<script>
  import { invalidateAll } from '$app/navigation';
  import { deserialize } from '$app/forms';
  import { Loader2, Upload, FileText, Download, CheckCircle2, AlertCircle } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button/index.js';
  import * as Dialog from '$lib/components/ui/dialog/index.js';

  /**
   * Two-step CSV import flow: upload → preview → confirm.
   * The drawer is purely presentational; the network calls go to the
   * SvelteKit form actions defined in tickets/+page.server.js, which
   * forward the multipart upload to Django.
   *
   * @type {{ open: boolean, onOpenChange?: (v: boolean) => void }}
   */
  let { open = $bindable(false), onOpenChange } = $props();

  /** @type {'select' | 'preview' | 'done'} */
  let step = $state('select');
  /** @type {File | null} */
  let file = $state(null);
  let dragOver = $state(false);
  let busy = $state(false);

  /** @type {{ summary: { total: number, valid: number, invalid: number }, valid: any[], errors: { row: number, field: string, message: string }[], header_error: string | null } | null} */
  let preview = $state(null);
  /** @type {string | null} */
  let formError = $state(null);
  /**
   * Per-row errors returned by the commit endpoint when the uploaded file
   * changed between preview and commit. Rendered in the same shape as preview
   * errors so users can act on them without re-running preview.
   * @type {{ row: number, field: string, message: string }[]}
   */
  let commitErrors = $state([]);

  /** @type {{ created: number, ids: string[] } | null} */
  let commitResult = $state(null);

  const TEMPLATE_HEADERS = [
    'name',
    'status',
    'priority',
    'description',
    'case_type',
    'account_name',
    'contact_emails',
    'assigned_emails',
    'team_names',
    'tags',
    'closed_on'
  ];
  const TEMPLATE_EXAMPLE = [
    'Login button broken',
    'New',
    'High',
    'Returns 500 on submit',
    'Incident',
    'Acme Corp',
    'pat@acme.test',
    'agent@yourco.com',
    'Support',
    'auth;urgent',
    ''
  ];

  function reset() {
    step = 'select';
    file = null;
    preview = null;
    commitResult = null;
    commitErrors = [];
    formError = null;
    busy = false;
  }

  $effect(() => {
    if (!open) reset();
  });

  function downloadTemplate() {
    const csv = [
      TEMPLATE_HEADERS.join(','),
      TEMPLATE_EXAMPLE.map((v) => (v.includes(',') ? `"${v}"` : v)).join(',')
    ].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'tickets-import-template.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  /** @param {{ row: number, field: string, message: string }[]} errs */
  function downloadErrorsList(errs) {
    if (!errs?.length) return;
    const rows = [['row', 'field', 'message'], ...errs.map((e) => [e.row, e.field, e.message])];
    const csv = rows
      .map((r) => r.map((c) => (String(c).includes(',') ? `"${c}"` : String(c))).join(','))
      .join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'tickets-import-errors.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  /** @param {Event & { currentTarget: HTMLInputElement }} e */
  function onFileChange(e) {
    const f = e.currentTarget.files?.[0];
    if (f) file = f;
  }

  /** @param {DragEvent} e */
  function onDrop(e) {
    e.preventDefault();
    dragOver = false;
    const f = e.dataTransfer?.files?.[0];
    if (f) file = f;
  }

  /**
   * The Node adapter refuses a body over its limit (512 KB unless the deploy
   * sets BODY_SIZE_LIMIT) with a plain-text 413 before the action runs, which
   * `deserialize` cannot parse.
   * @param {Response} res
   */
  async function readResult(res) {
    if (res.status === 413) {
      return {
        type: 'failure',
        data: { importError: 'This file is too large to upload. Split it into smaller files.' }
      };
    }
    return /** @type {any} */ (deserialize(await res.text()));
  }

  async function submitPreview() {
    if (!file) return;
    formError = null;
    commitErrors = [];
    busy = true;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetch('?/importPreview', { method: 'POST', body: fd });
      const result = await readResult(res);
      if (result.type === 'failure') {
        formError = String(result.data?.importError || 'Preview failed');
        return;
      }
      if (result.type === 'success' && result.data?.importPreview) {
        preview = result.data.importPreview;
        if (preview?.header_error) {
          formError = preview.header_error;
          return;
        }
        step = 'preview';
      } else {
        formError = 'Unexpected response from server';
      }
    } catch (err) {
      formError = err instanceof Error ? err.message : 'Preview failed';
    } finally {
      busy = false;
    }
  }

  async function submitCommit() {
    if (!file || !preview || preview.errors.length > 0) return;
    commitErrors = [];
    busy = true;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetch('?/importCommit', { method: 'POST', body: fd });
      const result = await readResult(res);
      if (result.type === 'failure') {
        formError = String(result.data?.importError || 'Import failed');
        const errs = result.data?.importErrors;
        if (Array.isArray(errs) && errs.length) {
          commitErrors = errs;
        }
        return;
      }
      if (result.type === 'success' && result.data?.importCommit) {
        commitResult = result.data.importCommit;
        step = 'done';
        await invalidateAll();
      }
    } catch (err) {
      formError = err instanceof Error ? err.message : 'Import failed';
    } finally {
      busy = false;
    }
  }
</script>

<Dialog.Root bind:open onOpenChange={(v) => onOpenChange?.(v)}>
  <Dialog.Content class="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-3xl">
    <Dialog.Header>
      <Dialog.Title class="flex items-center gap-2">
        <Upload class="h-4 w-4" />
        Import tickets from CSV
      </Dialog.Title>
      <Dialog.Description>
        Upload a CSV of tickets. We'll validate every row before writing anything. References
        (account, contacts, assignees) must already exist in your org.
      </Dialog.Description>
    </Dialog.Header>

    {#if step === 'select'}
      <div class="space-y-4">
        <button
          type="button"
          class="block w-full rounded-lg border-2 border-dashed border-[var(--border-default)] bg-[var(--surface-muted)] p-6 text-left transition-colors hover:bg-[var(--surface-default)] {dragOver
            ? 'border-[var(--color-primary-default)] bg-[var(--surface-default)]'
            : ''}"
          ondragover={(e) => {
            e.preventDefault();
            dragOver = true;
          }}
          ondragleave={() => (dragOver = false)}
          ondrop={onDrop}
          onclick={() => document.getElementById('ticket-import-file')?.click()}
        >
          <div class="flex items-center gap-3">
            <FileText class="h-8 w-8 shrink-0 text-[var(--text-secondary)]" />
            <div class="min-w-0">
              {#if file}
                <p class="truncate text-sm font-medium">{file.name}</p>
                <p class="text-xs text-[var(--text-secondary)]">
                  {(file.size / 1024).toFixed(1)} KB · click to replace or drop another file
                </p>
              {:else}
                <p class="text-sm font-medium">Drop a CSV file here, or click to choose</p>
                <p class="text-xs text-[var(--text-secondary)]">.csv only, up to 5 MB</p>
              {/if}
            </div>
          </div>
        </button>
        <input
          id="ticket-import-file"
          type="file"
          accept=".csv"
          class="hidden"
          onchange={onFileChange}
        />

        <div
          class="rounded-lg border border-[var(--border-default)] bg-[var(--surface-muted)] p-3 text-xs"
        >
          <p class="font-medium">CSV format</p>
          <p class="mt-1 text-[var(--text-secondary)]">
            Required headers: <code class="rounded bg-[var(--surface-default)] px-1">name</code>,
            <code class="rounded bg-[var(--surface-default)] px-1">status</code>,
            <code class="rounded bg-[var(--surface-default)] px-1">priority</code>. Optional:
            description, case_type, account_name, contact_emails, assigned_emails, team_names, tags
            (semicolon-separated), closed_on (YYYY-MM-DD).
          </p>
          <button
            type="button"
            class="mt-2 inline-flex items-center gap-1 text-[var(--color-primary-default)] hover:underline max-md:min-h-11"
            onclick={downloadTemplate}
          >
            <Download class="h-3.5 w-3.5" />Download CSV template
          </button>
        </div>

        {#if formError}
          <div
            class="flex items-start gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800"
          >
            <AlertCircle class="h-4 w-4 shrink-0" />
            <span>{formError}</span>
          </div>
        {/if}
      </div>

      <Dialog.Footer>
        <Button class="max-md:h-11" type="button" variant="outline" onclick={() => (open = false)}
          >Cancel</Button
        >
        <Button class="max-md:h-11" type="button" disabled={!file || busy} onclick={submitPreview}>
          {#if busy}<Loader2 class="mr-1 h-3.5 w-3.5 animate-spin" />{/if}
          Preview
        </Button>
      </Dialog.Footer>
    {:else if step === 'preview' && preview}
      {@const hasErrors = preview.errors.length > 0}
      <div class="space-y-4">
        <div class="flex flex-wrap items-center gap-2">
          <span
            class="inline-flex items-center gap-1 rounded-full bg-[var(--surface-muted)] px-3 py-1 text-xs font-medium"
          >
            Total: {preview.summary.total}
          </span>
          <span
            class="inline-flex items-center gap-1 rounded-full bg-green-50 px-3 py-1 text-xs font-medium text-green-800"
          >
            <CheckCircle2 class="h-3.5 w-3.5" />Valid: {preview.summary.valid}
          </span>
          {#if preview.summary.invalid > 0}
            <span
              class="inline-flex items-center gap-1 rounded-full bg-red-50 px-3 py-1 text-xs font-medium text-red-800"
            >
              <AlertCircle class="h-3.5 w-3.5" />Invalid: {preview.summary.invalid}
            </span>
          {/if}
        </div>

        {#if preview.valid.length > 0}
          <div class="max-h-48 overflow-auto rounded-md border border-[var(--border-default)]">
            <table class="w-full text-xs">
              <thead class="sticky top-0 bg-[var(--surface-muted)]">
                <tr>
                  <th class="px-2 py-1 text-left font-medium">#</th>
                  <th class="px-2 py-1 text-left font-medium">Name</th>
                  <th class="px-2 py-1 text-left font-medium">Status</th>
                  <th class="px-2 py-1 text-left font-medium">Priority</th>
                  <th class="px-2 py-1 text-left font-medium">Account</th>
                </tr>
              </thead>
              <tbody>
                {#each preview.valid.slice(0, 20) as row (row.row)}
                  <tr class="border-t border-[var(--border-default)]">
                    <td class="px-2 py-1 text-[var(--text-secondary)]">{row.row}</td>
                    <td class="max-w-[200px] truncate px-2 py-1">{row.name}</td>
                    <td class="px-2 py-1">{row.status}</td>
                    <td class="px-2 py-1">{row.priority}</td>
                    <td class="max-w-[140px] truncate px-2 py-1 text-[var(--text-secondary)]">
                      {row.account_id ? '✓' : '—'}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
            {#if preview.valid.length > 20}
              <div
                class="border-t border-[var(--border-default)] bg-[var(--surface-muted)] px-2 py-1 text-center text-[10px] text-[var(--text-secondary)]"
              >
                Showing first 20 of {preview.valid.length} valid rows
              </div>
            {/if}
          </div>
        {/if}

        {#if hasErrors}
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <p class="text-sm font-medium text-red-800">
                {preview.errors.length} error{preview.errors.length === 1 ? '' : 's'}, fix the CSV
                before importing
              </p>
              <button
                type="button"
                class="inline-flex items-center gap-1 text-xs text-[var(--color-primary-default)] hover:underline max-md:min-h-11"
                onclick={() => downloadErrorsList(preview?.errors ?? [])}
              >
                <Download class="h-3 w-3" />Download errors
              </button>
            </div>
            <div class="max-h-40 overflow-auto rounded-md border border-red-200 bg-red-50">
              <table class="w-full text-xs">
                <thead class="sticky top-0 bg-red-100">
                  <tr>
                    <th class="px-2 py-1 text-left font-medium">Row</th>
                    <th class="px-2 py-1 text-left font-medium">Field</th>
                    <th class="px-2 py-1 text-left font-medium">Problem</th>
                  </tr>
                </thead>
                <tbody>
                  {#each preview.errors as err (`${err.row}-${err.field}-${err.message}`)}
                    <tr class="border-t border-red-200">
                      <td class="px-2 py-1 font-mono">{err.row}</td>
                      <td class="px-2 py-1">{err.field}</td>
                      <td class="px-2 py-1">{err.message}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </div>
        {/if}

        {#if commitErrors.length > 0}
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <p class="text-sm font-medium text-red-800">
                Server rejected {commitErrors.length} row{commitErrors.length === 1 ? '' : 's'} during
                import. The file may have changed since preview
              </p>
              <button
                type="button"
                class="inline-flex items-center gap-1 text-xs text-[var(--color-primary-default)] hover:underline max-md:min-h-11"
                onclick={() => downloadErrorsList(commitErrors)}
              >
                <Download class="h-3 w-3" />Download errors
              </button>
            </div>
            <div class="max-h-40 overflow-auto rounded-md border border-red-200 bg-red-50">
              <table class="w-full text-xs">
                <thead class="sticky top-0 bg-red-100">
                  <tr>
                    <th class="px-2 py-1 text-left font-medium">Row</th>
                    <th class="px-2 py-1 text-left font-medium">Field</th>
                    <th class="px-2 py-1 text-left font-medium">Problem</th>
                  </tr>
                </thead>
                <tbody>
                  {#each commitErrors as err (`${err.row}-${err.field}-${err.message}`)}
                    <tr class="border-t border-red-200">
                      <td class="px-2 py-1 font-mono">{err.row}</td>
                      <td class="px-2 py-1">{err.field}</td>
                      <td class="px-2 py-1">{err.message}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </div>
        {/if}

        {#if formError}
          <div
            class="flex items-start gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800"
          >
            <AlertCircle class="h-4 w-4 shrink-0" />
            <span>{formError}</span>
          </div>
        {/if}
      </div>

      <Dialog.Footer>
        <Button class="max-md:h-11" type="button" variant="outline" onclick={() => reset()}
          >Back</Button
        >
        <Button
          class="max-md:h-11"
          type="button"
          disabled={hasErrors || preview.summary.valid === 0 || busy}
          onclick={submitCommit}
        >
          {#if busy}<Loader2 class="mr-1 h-3.5 w-3.5 animate-spin" />{/if}
          Import {preview.summary.valid} ticket{preview.summary.valid === 1 ? '' : 's'}
        </Button>
      </Dialog.Footer>
    {:else if step === 'done' && commitResult}
      <div class="space-y-4 py-2">
        <div class="flex items-center gap-3 rounded-lg border border-green-200 bg-green-50 p-4">
          <CheckCircle2 class="h-6 w-6 text-green-700" />
          <div>
            <p class="text-sm font-medium text-green-900">
              Imported {commitResult.created} ticket{commitResult.created === 1 ? '' : 's'}
            </p>
            <p class="text-xs text-green-800">The tickets list has been refreshed.</p>
          </div>
        </div>
      </div>
      <Dialog.Footer>
        <Button class="max-md:h-11" type="button" onclick={() => (open = false)}>Close</Button>
      </Dialog.Footer>
    {/if}
  </Dialog.Content>
</Dialog.Root>
