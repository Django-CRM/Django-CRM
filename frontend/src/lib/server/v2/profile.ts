/**
 * Your own account: the wiring behind /v2/profile.
 *
 * Server-only. Read in three calls, none of which reaches past the caller:
 *   - `GET /profile/`         the current profile (own; request.profile),
 *                             extended with the caller's team names.
 *   - `GET /org/`             every org the caller is a member of, scoped to
 *                             `Profile.objects.filter(user=request.user)`, so it
 *                             is their memberships, never a directory of orgs.
 *   - `GET /profile/tokens/`  the caller's personal access tokens, counted here
 *                             into the "active" number the page shows.
 *
 * The one field the page lets you change is name + phone, over
 * `PATCH /profile/`. Role, org and the access flags are shown but never posted:
 * they decide permissions and are an admin's to set (the endpoint refuses them
 * regardless. See ProfileSelfUpdateSerializer). Switching org is a real action
 * and re-issues the JWT rather than editing a field; that lives in the page's
 * `switchOrg` action, not here.
 */
import { apiRequest } from '#lib/api-helpers.js';
import type { Cookies } from '@sveltejs/kit';
import type { components } from '#lib/api-schema.js';

type Schemas = components['schemas'];
type Role = Schemas['ProfileRoleEnum'];
type OwnProfile = {
  id: string;
  user_details: { first_name: string; last_name: string; email: string };
  role: Role;
  phone: string;
  teams: string[];
  joined_at: string | null;
  last_login: string | null;
  orgs: { id: string; name: string; role: Role; is_current: boolean }[];
  active_token_count: number;
  lead_alerts_email: boolean;
  lead_alerts_push: boolean;
};

/** Split a single display name into first/last for the page's header.
 *  The backend stores one `name` on User; the page shows "First Last". */
function splitName(name: string, email: string) {
  const trimmed = (name || '').trim();
  if (!trimmed) {
    const local = (email || '').split('@')[0] || 'You';
    return { first_name: local, last_name: '' };
  }
  const sp = trimmed.indexOf(' ');
  if (sp === -1) return { first_name: trimmed, last_name: '' };
  return { first_name: trimmed.slice(0, sp), last_name: trimmed.slice(sp + 1) };
}

/** A personal access token is "active" when it is neither revoked nor expired. */
function isActiveToken(t: Schemas['PersonalAccessTokenList']) {
  if (t.revoked_at) return false;
  if (t.expires_at && new Date(t.expires_at).getTime() <= Date.now()) return false;
  return true;
}

/** The current user's account, shaped for the page. */
export async function getProfile({
  cookies
}: {
  cookies: Cookies;
}): Promise<{ profile: OwnProfile; org: { name: string } }> {
  const currentOrgId = cookies.get('org') || '';

  const meCall: Promise<Schemas['ProfileViewResponse']> = apiRequest('/profile/', {}, { cookies });
  const orgCall: Promise<Schemas['OrgListResponse']> = apiRequest('/org/', {}, { cookies });
  const tokenCall: Promise<Schemas['AccessTokenListResponse']> = apiRequest(
    '/profile/tokens/',
    {},
    { cookies }
  );
  const [me, orgResp, tokenResp] = await Promise.all([meCall, orgCall, tokenCall]);

  const u: Schemas['OwnProfile'] = me.user_obj;
  const ud = u.user_details;
  const name = splitName(ud.name, ud.email);

  const memberships = orgResp.profile_org_list || [];
  const orgs = memberships
    .map((m) => ({
      id: m.org?.id ?? '',
      name: m.org?.name ?? '',
      role: m.role,
      is_current: String(m.org?.id ?? '') === String(currentOrgId)
    }))
    .filter((o) => o.id);

  // Current org name: the membership the JWT points at, falling back to the
  // first one so the header is never blank if the `org` cookie is missing.
  const current = orgs.find((o) => o.is_current) || orgs[0] || null;

  const tokens = tokenResp.tokens || [];
  const active_token_count = tokens.filter(isActiveToken).length;

  return {
    profile: {
      id: u.id,
      user_details: {
        first_name: name.first_name,
        last_name: name.last_name,
        email: ud.email || ''
      },
      role: u.role,
      phone: u.phone || '',
      teams: u.teams || [],
      joined_at: u.date_of_joining || u.created_at || null,
      last_login: ud.last_login || null,
      orgs,
      active_token_count,
      // An API older than these fields leaves them out; both default on there.
      lead_alerts_email: u.lead_alerts_email ?? true,
      lead_alerts_push: u.lead_alerts_push ?? true
    },
    org: { name: current?.name || '' }
  };
}
