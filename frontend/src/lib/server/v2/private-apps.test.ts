import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: unknown[]) => apiRequest(...a)
}));

const {
  readPrivateApps,
  resetPrivateAppsAbsentMemo,
  uploadManifest,
  readUpload,
  applyUpload,
  removePrivateApp,
  replacePrivateKey,
  isUploadId,
  isPrivateAppId
} = await import('./private-apps.js');

const cookies = { get: () => 'token' } as never;
const event = { cookies };
const failure = (status: number, body = {}) => Object.assign(new Error('x'), { status, body });

beforeEach(() => {
  apiRequest.mockReset();
  resetPrivateAppsAbsentMemo();
});

describe('readPrivateApps', () => {
  it('lists the apps', async () => {
    apiRequest.mockResolvedValueOnce({ apps: [{ id: 'crm-sync' }] });
    expect(await readPrivateApps(event)).toEqual({
      offered: true,
      forbidden: false,
      apps: [{ id: 'crm-sync' }]
    });
    expect(apiRequest.mock.calls[0][0]).toBe('/private-apps/');
  });

  it('is not offered on a 404 and does not ask again within 5 minutes', async () => {
    apiRequest.mockRejectedValueOnce(failure(404));
    expect(await readPrivateApps(event)).toEqual({ offered: false });
    expect(await readPrivateApps(event)).toEqual({ offered: false });
    expect(apiRequest).toHaveBeenCalledTimes(1);
  });

  it('is forbidden on a 403, with no apps', async () => {
    apiRequest.mockRejectedValueOnce(failure(403));
    expect(await readPrivateApps(event)).toEqual({ offered: true, forbidden: true, apps: [] });
  });

  it('lets any other failure through', async () => {
    apiRequest.mockRejectedValueOnce(failure(500));
    await expect(readPrivateApps(event)).rejects.toMatchObject({ status: 500 });
  });
});

describe('calls', () => {
  it('uploads the text as the manifest', async () => {
    apiRequest.mockResolvedValueOnce({ upload: { id: 'u1' } });
    await uploadManifest(event, '{"id":"a"}');
    expect(apiRequest).toHaveBeenCalledWith(
      '/private-apps/uploads/',
      expect.objectContaining({ method: 'POST', body: { manifest: '{"id":"a"}' } }),
      { cookies }
    );
  });

  it('reads, applies, removes and replaces by id', async () => {
    apiRequest.mockResolvedValue({});
    await readUpload(event, 'abc123');
    await applyUpload(event, 'abc123', true);
    await removePrivateApp(event, 'crm-sync');
    await replacePrivateKey(event, 'crm-sync');
    const calls = apiRequest.mock.calls.map((c) => [c[0], c[1]?.method, c[1]?.body]);
    expect(calls).toEqual([
      ['/private-apps/uploads/abc123/', undefined, undefined],
      ['/private-apps/uploads/abc123/apply/', 'POST', { trust: true }],
      ['/private-apps/crm-sync/remove/', 'POST', undefined],
      ['/private-apps/crm-sync/replace-key/', 'POST', undefined]
    ]);
  });

  it('refuses malformed ids before any request', async () => {
    await expect(readUpload(event, '../x')).rejects.toMatchObject({ status: 404 });
    await expect(applyUpload(event, 'a/b', false)).rejects.toMatchObject({ status: 404 });
    await expect(removePrivateApp(event, '')).rejects.toMatchObject({ status: 404 });
    await expect(replacePrivateKey(event, 'x y')).rejects.toMatchObject({ status: 404 });
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('id checks', () => {
  it('accepts public ids and refuses the rest', () => {
    expect(isUploadId('abc_123-x')).toBe(true);
    expect(isPrivateAppId('crm-sync')).toBe(true);
    for (const bad of ['', 'a/b', 'a b', 5, null, undefined, 'x'.repeat(65)]) {
      expect(isUploadId(bad)).toBe(false);
      expect(isPrivateAppId(bad)).toBe(false);
    }
  });
});
