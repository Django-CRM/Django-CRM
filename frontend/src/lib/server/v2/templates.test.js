import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({
  apiRequest: (/** @type {any[]} */ ...args) => apiRequest(...args)
}));

const {
  createInvoiceTemplate,
  getInvoiceTemplateForEdit,
  updateInvoiceTemplate,
  CREATE_FIELDS,
  UPDATE_FIELDS
} = await import('./templates.js');

const cookies = /** @type {any} */ ({});

describe('createInvoiceTemplate', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({ id: 't1' });
  });

  it('never accepts the raw markup fields', () => {
    expect(CREATE_FIELDS).not.toContain('template_html');
    expect(CREATE_FIELDS).not.toContain('template_css');
  });

  it('posts to the templates collection', async () => {
    await createInvoiceTemplate({ cookies }, { name: 'Clean' });
    const [url, options] = apiRequest.mock.calls[0];
    expect(url).toBe('/invoices/templates/');
    expect(options.method).toBe('POST');
  });

  it('drops the markup fields even when a caller forces them in', async () => {
    await createInvoiceTemplate(
      { cookies },
      { name: 'Clean', template_html: '<script>x</script>', template_css: 'body{}' }
    );
    const [, options] = apiRequest.mock.calls[0];
    const sent = options.body;
    expect(sent.template_html).toBeUndefined();
    expect(sent.template_css).toBeUndefined();
  });

  it('drops server-derived keys', async () => {
    await createInvoiceTemplate({ cookies }, { name: 'Clean', org: 'attacker-org', id: 'forged' });
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.org).toBeUndefined();
    expect(options.body.id).toBeUndefined();
  });

  it('requires a name', async () => {
    await expect(createInvoiceTemplate({ cookies }, { name: '  ' })).rejects.toThrow();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('rejects a colour that is not a hex value, which the server does not check', async () => {
    await expect(
      createInvoiceTemplate({ cookies }, { name: 'Clean', primary_color: 'purple' })
    ).rejects.toThrow();
    await expect(
      createInvoiceTemplate({ cookies }, { name: 'Clean', secondary_color: '#12' })
    ).rejects.toThrow();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('accepts a valid hex colour', async () => {
    await createInvoiceTemplate(
      { cookies },
      { name: 'Clean', primary_color: '#3B82F6', secondary_color: '#1e40af' }
    );
    expect(apiRequest).toHaveBeenCalled();
  });

  it('omits an empty colour rather than sending it as an empty string', async () => {
    // Neither colour field has `blank=True` on the model, so a literal `''`
    // earns a raw 400 ("This field may not be blank"). Omitting the key lets
    // the model default apply instead.
    await createInvoiceTemplate(
      { cookies },
      { name: 'Clean', primary_color: '', secondary_color: '#1e40af' }
    );
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.primary_color).toBeUndefined();
    expect(options.body.secondary_color).toBe('#1e40af');
  });

  it('sends FormData when a logo file is supplied', async () => {
    const file = new File(['x'], 'logo.png', { type: 'image/png' });
    await createInvoiceTemplate({ cookies }, { name: 'Clean' }, file);
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get('name')).toBe('Clean');
    expect(options.body.get('logo')).toBe(file);
  });

  it('sends a plain object when there is no logo', async () => {
    await createInvoiceTemplate({ cookies }, { name: 'Clean' });
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body).not.toBeInstanceOf(FormData);
    expect(options.body.name).toBe('Clean');
  });

  it('unwraps the created template from the envelope', async () => {
    apiRequest.mockResolvedValue({
      error: false,
      message: 'Template created',
      template: { id: 't1', name: 'Clean' }
    });
    const result = await createInvoiceTemplate({ cookies }, { name: 'Clean' });
    expect(result).toEqual({ id: 't1', name: 'Clean' });
  });

  it('falls back to the raw response if it has no template envelope', async () => {
    apiRequest.mockResolvedValue({ id: 't1', name: 'Clean' });
    const result = await createInvoiceTemplate({ cookies }, { name: 'Clean' });
    expect(result).toEqual({ id: 't1', name: 'Clean' });
  });
});

/**
 * `getInvoiceTemplateForEdit`'s admin gate comes from `viewerIsAdmin`, which
 * decodes the `is_organization_admin` claim straight out of the `jwt_access`
 * cookie with no network call. A `{ get: () => 'token' }` stub cannot drive it:
 * `'token'` has no `.` to split on, so `viewerIsAdmin` catches that and returns
 * false and every test would see a non-admin. This builds a JWT-shaped string
 * with a real base64url payload so the decode path actually runs. The claims
 * are the pair the API signs: `role`, and the admin fact derived from it (or
 * from a superuser's membership, `isAdmin` below).
 *
 * @param {string} role
 * @param {boolean} [isAdmin]
 */
function eventWithRole(role, isAdmin = role === 'ADMIN') {
  const payload = Buffer.from(
    JSON.stringify({ role, is_organization_admin: isAdmin }),
    'utf-8'
  ).toString('base64url');
  const token = `h.${payload}.s`;
  return /** @type {any} */ ({
    cookies: { get: (/** @type {string} */ name) => (name === 'jwt_access' ? token : null) }
  });
}

const SAVED = {
  id: 't1',
  name: 'House style',
  primary_color: '#111111',
  secondary_color: '#222222',
  default_notes: 'notes',
  default_terms: 'terms',
  footer_text: 'footer',
  template_html: '<h1>{{ invoice.number }}</h1>',
  template_css: 'h1{color:red}',
  is_default: true,
  logo: '/media/logo.png'
};

describe('getInvoiceTemplateForEdit', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue(SAVED);
  });

  it('reads the dedicated editor route, not the detail route', async () => {
    await getInvoiceTemplateForEdit(eventWithRole('ADMIN'), 't1');
    const [url] = apiRequest.mock.calls[0];
    expect(url).toBe('/invoices/templates/t1/editor/');
  });

  it('returns the raw markup, which is the whole point of the route', async () => {
    const data = await getInvoiceTemplateForEdit(eventWithRole('ADMIN'), 't1');
    expect(data.can_edit).toBe(true);
    expect(data.template.template_html).toBe(SAVED.template_html);
    expect(data.template.template_css).toBe(SAVED.template_css);
  });

  it('refuses a non-admin without fetching anything', async () => {
    const data = await getInvoiceTemplateForEdit(eventWithRole('USER'), 't1');
    expect(data).toEqual({ can_edit: false });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('lets a USER-role superuser in, because the admin fact says so', async () => {
    const data = await getInvoiceTemplateForEdit(eventWithRole('USER', true), 't1');
    expect(data.can_edit).toBe(true);
  });

  it('refuses a caller with no readable role', async () => {
    const data = await getInvoiceTemplateForEdit(
      /** @type {any} */ ({ cookies: { get: () => null } }),
      't1'
    );
    expect(data).toEqual({ can_edit: false });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('carries every field the form has an input for', async () => {
    const { template } = await getInvoiceTemplateForEdit(eventWithRole('ADMIN'), 't1');
    for (const key of UPDATE_FIELDS) {
      expect(template[key]).toBeDefined();
    }
  });
});

describe('updateInvoiceTemplate', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({ id: 't1' });
  });

  it('PUTs to the template detail route', async () => {
    await updateInvoiceTemplate({ cookies }, 't1', { name: 'Clean' });
    const [url, options] = apiRequest.mock.calls[0];
    expect(url).toBe('/invoices/templates/t1/');
    expect(options.method).toBe('PUT');
  });

  it('does send the markup, unlike create', async () => {
    await updateInvoiceTemplate({ cookies }, 't1', {
      name: 'Clean',
      template_html: '<h1>x</h1>',
      template_css: 'h1{}'
    });
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.template_html).toBe('<h1>x</h1>');
    expect(options.body.template_css).toBe('h1{}');
  });

  it('sends empty markup rather than dropping it, so clearing works', async () => {
    await updateInvoiceTemplate({ cookies }, 't1', { name: 'Clean', template_html: '' });
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.template_html).toBe('');
  });

  it('never sends is_default, which would demote the org default', async () => {
    expect(UPDATE_FIELDS).not.toContain('is_default');
    await updateInvoiceTemplate({ cookies }, 't1', { name: 'Clean', is_default: false });
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.is_default).toBeUndefined();
  });

  it('drops server-derived keys', async () => {
    await updateInvoiceTemplate({ cookies }, 't1', {
      name: 'Clean',
      org: 'attacker-org',
      id: 'forged'
    });
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.org).toBeUndefined();
    expect(options.body.id).toBeUndefined();
  });

  it('requires a name', async () => {
    await expect(updateInvoiceTemplate({ cookies }, 't1', { name: '  ' })).rejects.toThrow();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('rejects a colour that is not a hex value', async () => {
    await expect(
      updateInvoiceTemplate({ cookies }, 't1', { name: 'Clean', primary_color: 'purple' })
    ).rejects.toThrow();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('omits an empty colour, which the model would 400 on', async () => {
    await updateInvoiceTemplate({ cookies }, 't1', { name: 'Clean', primary_color: '' });
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.primary_color).toBeUndefined();
  });

  it('sends multipart when a logo is supplied', async () => {
    const file = new File(['x'], 'logo.png', { type: 'image/png' });
    await updateInvoiceTemplate({ cookies }, 't1', { name: 'Clean' }, file);
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get('logo')).toBe(file);
  });
});
