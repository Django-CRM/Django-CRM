<script>
  /**
   * The fields a webhook is made of, shared by the create panel and the edit
   * panel so the two cannot drift. The event checkboxes are drawn from the
   * catalogue the API returns, grouped by module; the serializer is what
   * refuses a name outside it.
   *
   * @type {{
   *   catalogue: { module: string, label: string, events: string[] }[],
   *   values?: { url?: string, description?: string, format?: string, events?: string[] },
   *   idPrefix: string
   * }}
   */
  let { catalogue, values = {}, idPrefix } = $props();

  let chosen = $derived(new Set(values.events ?? []));

  /** "ticket.comment_added" reads as "Comment added". @param {string} name */
  const actionLabel = (name) => {
    const action = name.split('.')[1] ?? name;
    return (action.charAt(0).toUpperCase() + action.slice(1)).replaceAll('_', ' ');
  };
</script>

<div class="wh-fields">
  <div class="v2-field">
    <label for="{idPrefix}-url">Endpoint URL</label>
    <input
      id="{idPrefix}-url"
      name="url"
      type="url"
      required
      maxlength="500"
      inputmode="url"
      autocomplete="off"
      class="v2-input"
      placeholder="https://hooks.zapier.com/hooks/catch/..."
      value={values.url ?? ''}
    />
    <span class="v2-sub" style="font-size:11.5px">
      HTTPS on the standard port, reachable from the public internet.
    </span>
  </div>

  <div class="v2-field">
    <label for="{idPrefix}-description">What is it for?</label>
    <input
      id="{idPrefix}-description"
      name="description"
      maxlength="255"
      class="v2-input"
      placeholder="e.g. Zapier: new leads to the sales sheet"
      value={values.description ?? ''}
    />
  </div>

  <div class="v2-field">
    <label for="{idPrefix}-format">Send as</label>
    <select id="{idPrefix}-format" name="format" class="v2-input">
      <option value="json" selected={(values.format ?? 'json') === 'json'}>
        Signed JSON (Zapier, n8n, your own code)
      </option>
      <option value="slack" selected={values.format === 'slack'}>
        Slack message (incoming webhook URL)
      </option>
    </select>
  </div>

  <fieldset class="wh-events">
    <legend class="v2-label">Events</legend>
    {#each catalogue as group (group.module)}
      <div class="wh-group">
        <div class="wh-group-label">{group.label}</div>
        <div class="wh-boxes">
          {#each group.events as name (name)}
            <label class="wh-box">
              <input type="checkbox" name="events" value={name} checked={chosen.has(name)} />
              <span>{actionLabel(name)}</span>
            </label>
          {/each}
        </div>
      </div>
    {/each}
  </fieldset>
</div>

<style>
  .wh-fields {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .wh-events {
    border: 0;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .wh-group-label {
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .wh-boxes {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 14px;
  }
  /* 44px tall so each box is a thumb-sized target on a phone. */
  .wh-box {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    font-size: 13px;
    font-weight: 400;
  }
  .wh-box input {
    width: 18px;
    height: 18px;
  }
</style>
