import { resolve } from '$app/paths';
/**
 * API Client for Django REST Framework Backend
 *
 * This module provides a centralized API client for making requests to the Django backend.
 * It handles JWT authentication, organization context, and common API patterns.
 *
 * @module lib/api
 */

import { env } from '$env/dynamic/public';
import { goto } from '$app/navigation';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// API Base URL from environment variables
// Note: VITE_ prefix is required for client-side env vars
const API_BASE_URL = env.PUBLIC_DJANGO_API_URL
  ? `${env.PUBLIC_DJANGO_API_URL}/api`
  : 'http://localhost:8000/api';

/**
 * Storage keys for tokens and org
 */
const STORAGE_KEYS = {
  ACCESS_TOKEN: 'access_token',
  REFRESH_TOKEN: 'refresh_token',
  ORG_ID: 'org_id',
  USER: 'user'
};

/**
 * Get token from localStorage (client-side) or return null (server-side)
 * @param {string} key - Storage key
 * @returns {string|null}
 */
function getFromStorage(key) {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(key);
}

/**
 * Set token in localStorage (client-side only)
 * @param {string} key - Storage key
 * @param {string} value - Value to store
 */
function setInStorage(key, value) {
  if (typeof window !== 'undefined') {
    localStorage.setItem(key, value);
  }
}

/**
 * Remove token from localStorage (client-side only)
 * @param {string} key - Storage key
 */
function removeFromStorage(key) {
  if (typeof window !== 'undefined') {
    localStorage.removeItem(key);
  }
}

/**
 * Clear all auth data from storage
 */
export function clearAuthData() {
  removeFromStorage(STORAGE_KEYS.ACCESS_TOKEN);
  removeFromStorage(STORAGE_KEYS.REFRESH_TOKEN);
  removeFromStorage(STORAGE_KEYS.ORG_ID);
  removeFromStorage(STORAGE_KEYS.USER);
}

/**
 * Get current access token
 * @returns {string|null}
 */
export function getAccessToken() {
  return getFromStorage(STORAGE_KEYS.ACCESS_TOKEN);
}

/**
 * Get current refresh token
 * @returns {string|null}
 */
export function getRefreshToken() {
  return getFromStorage(STORAGE_KEYS.REFRESH_TOKEN);
}

/**
 * Get current organization ID
 * @returns {string|null}
 */
export function getOrgId() {
  return getFromStorage(STORAGE_KEYS.ORG_ID);
}

/**
 * Set organization ID
 * @param {string} orgId - Organization UUID
 */
export function setOrgId(orgId) {
  setInStorage(STORAGE_KEYS.ORG_ID, orgId);
}

/**
 * Get current user data
 * @returns {Object|null}
 */
export function getCurrentUser() {
  const userData = getFromStorage(STORAGE_KEYS.USER);
  return userData ? JSON.parse(userData) : null;
}

/**
 * In-flight refresh shared by concurrent callers, or null when idle.
 * @type {Promise<string|null>|null}
 */
let refreshInFlight = null;

/**
 * Refresh the access token using refresh token.
 *
 * The backend rotates refresh tokens: the token we send is blacklisted and a
 * replacement comes back, so concurrent callers must not each spend the same
 * one. Whichever request landed second would get a 401 and log the user out,
 * so all callers share a single in-flight refresh.
 *
 * @returns {Promise<string|null>} New access token or null if refresh failed
 */
async function refreshAccessToken() {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = performTokenRefresh().finally(() => {
    refreshInFlight = null;
  });

  return refreshInFlight;
}

/**
 * Perform the actual refresh round-trip. Use refreshAccessToken() instead.
 * It deduplicates concurrent callers.
 * @returns {Promise<string|null>} New access token or null if refresh failed
 */
async function performTokenRefresh() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return null;
  }

  try {
    const response = await fetch(`${API_BASE_URL}/auth/refresh-token/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ refresh: refreshToken })
    });

    if (response.ok) {
      const data = await response.json();
      if (!data.access) {
        clearAuthData();
        return null;
      }
      setInStorage(STORAGE_KEYS.ACCESS_TOKEN, data.access);
      // Persist the rotated refresh token. The one we just spent is
      // blacklisted server-side, so keeping it would 401 the next refresh.
      if (data.refresh) {
        setInStorage(STORAGE_KEYS.REFRESH_TOKEN, data.refresh);
      }
      return data.access;
    }

    // Refresh token expired or invalid
    clearAuthData();
    if (typeof window !== 'undefined') {
      goto(resolve('/login'));
    }
    return null;
  } catch (error) {
    console.error('Token refresh failed:', error);
    clearAuthData();
    return null;
  }
}

/**
 * Make an authenticated API request
 *
 * @param {string} endpoint - API endpoint (e.g., '/accounts/', '/leads/123/')
 * @param {{ method?: string, body?: Record<string, unknown> | null, headers?: Record<string, string>, requiresAuth?: boolean }} [options] - Fetch options
 * @returns {Promise<any>} Response data
 * @throws {Error} If request fails
 */
export async function apiRequest(endpoint, options = {}) {
  const { method = 'GET', body = null, headers = {}, requiresAuth = true } = options;

  const url = `${API_BASE_URL}${endpoint}`;

  // Build headers
  /** @type {Record<string, string>} */
  const requestHeaders = {
    'Content-Type': 'application/json',
    ...headers
  };

  // Add authentication if required
  // Note: Organization context is now embedded in JWT token, not sent as header
  if (requiresAuth) {
    const accessToken = getAccessToken();
    if (accessToken) {
      requestHeaders['Authorization'] = `Bearer ${accessToken}`;
    }
  }

  // Build fetch options
  /** @type {RequestInit} */
  const fetchOptions = {
    method,
    headers: requestHeaders
  };

  if (body && method !== 'GET') {
    fetchOptions.body = JSON.stringify(body);
  }

  try {
    let response = await fetch(url, fetchOptions);

    // If unauthorized and we have a refresh token, try to refresh
    if (response.status === 401 && requiresAuth) {
      const newAccessToken = await refreshAccessToken();
      if (newAccessToken) {
        // Retry request with new token
        requestHeaders['Authorization'] = `Bearer ${newAccessToken}`;
        fetchOptions.headers = requestHeaders;
        response = await fetch(url, fetchOptions);
      }
    }

    // Handle response
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.detail || errorData.error || `HTTP ${response.status}: ${response.statusText}`
      );
    }

    // Return JSON response
    return await response.json();
  } catch (error) {
    console.error(`API request failed: ${method} ${endpoint}`, error);
    throw error;
  }
}

/**
 * Authentication API methods
 */
export const auth = {
  /**
   * Switch to a different organization
   * This issues new JWT tokens with the new org context
   * @param {string} orgId - Organization UUID to switch to
   * @returns {Promise<any>} New tokens and org data
   */
  async switchOrg(orgId) {
    if (!orgId || !UUID_RE.test(orgId)) {
      throw new Error('Invalid organization ID format');
    }
    // Hand over the outgoing refresh token so the backend retires it; we replace
    // it below either way, and leaving it live keeps a stolen copy working
    // against the previous org.
    const outgoingRefresh = getRefreshToken();
    /** @type {any} */
    const data = await apiRequest('/auth/switch-org/', {
      method: 'POST',
      body: outgoingRefresh ? { org_id: orgId, refresh: outgoingRefresh } : { org_id: orgId }
    });

    // Update tokens with new org context
    setInStorage(STORAGE_KEYS.ACCESS_TOKEN, data.access_token);
    setInStorage(STORAGE_KEYS.REFRESH_TOKEN, data.refresh_token);
    setInStorage(STORAGE_KEYS.ORG_ID, data.current_org.id);

    return data;
  },

  /**
   * Get current user data
   * @returns {Promise<Object>} Current user with organizations
   */
  async me() {
    const data = await apiRequest('/auth/me/');

    // Update stored user data
    setInStorage(STORAGE_KEYS.USER, JSON.stringify(data));

    return data;
  },

  /**
   * Get profile for current organization
   * @returns {Promise<Object>} Profile data for current org
   */
  async profile() {
    return await apiRequest('/auth/profile/');
  },

  /**
   * Logout (clear local data)
   * Note: Django simplejwt doesn't have server-side logout by default
   * Token blacklisting can be added later if needed
   */
  logout() {
    clearAuthData();
    if (typeof window !== 'undefined') {
      goto(resolve('/login'));
    }
  }
};

/**
 * Generic CRUD API factory
 * Creates standard CRUD methods for an entity
 *
 * @param {string} entityPath - Entity path (e.g., 'accounts', 'leads')
 * @returns {Object} CRUD methods
 */
function createCrudApi(entityPath) {
  return {
    /**
     * List entities with pagination and filters
     * @param {Record<string, string>} [params] - Query parameters
     * @returns {Promise<any>} Paginated list of entities
     */
    async list(params = {}) {
      const queryString = new URLSearchParams(params).toString();
      const endpoint = `/${entityPath}/${queryString ? `?${queryString}` : ''}`;
      return await apiRequest(endpoint);
    },

    /**
     * Get a single entity by ID
     * @param {string} id - Entity UUID
     * @returns {Promise<any>} Entity data
     */
    async get(id) {
      return await apiRequest(`/${entityPath}/${id}/`);
    },

    /**
     * Create a new entity
     * @param {Record<string, unknown>} data - Entity data
     * @returns {Promise<any>} Created entity
     */
    async create(data) {
      return await apiRequest(`/${entityPath}/`, {
        method: 'POST',
        body: data
      });
    },

    /**
     * Update an entity
     * @param {string} id - Entity UUID
     * @param {Record<string, unknown>} data - Updated entity data
     * @returns {Promise<any>} Updated entity
     */
    async update(id, data) {
      return await apiRequest(`/${entityPath}/${id}/`, {
        method: 'PUT',
        body: data
      });
    },

    /**
     * Partially update an entity
     * @param {string} id - Entity UUID
     * @param {Record<string, unknown>} data - Partial entity data
     * @returns {Promise<any>} Updated entity
     */
    async patch(id, data) {
      return await apiRequest(`/${entityPath}/${id}/`, {
        method: 'PATCH',
        body: data
      });
    },

    /**
     * Delete an entity
     * @param {string} id - Entity UUID
     * @returns {Promise<any>}
     */
    async delete(id) {
      return await apiRequest(`/${entityPath}/${id}/`, {
        method: 'DELETE'
      });
    },

    /**
     * Get comments for an entity
     * @param {string} id - Entity UUID
     * @returns {Promise<any>} List of comments
     */
    async getComments(id) {
      return await apiRequest(`/${entityPath}/comment/${id}/`);
    },

    /**
     * Add comment to an entity
     * @param {string} id - Entity UUID
     * @param {string} comment - Comment text
     * @returns {Promise<any>} Created comment
     */
    async addComment(id, comment) {
      return await apiRequest(`/${entityPath}/comment/${id}/`, {
        method: 'POST',
        body: { comment }
      });
    },

    /**
     * Delete a comment
     * @param {string} commentId - Comment UUID
     * @returns {Promise<any>} Response
     */
    async deleteComment(commentId) {
      return await apiRequest(`/${entityPath}/comment/${commentId}/`, {
        method: 'DELETE'
      });
    },

    /**
     * Get attachments for an entity
     * @param {string} id - Entity UUID
     * @returns {Promise<any>} List of attachments
     */
    async getAttachments(id) {
      return await apiRequest(`/${entityPath}/attachment/${id}/`);
    }
  };
}

/**
 * CRM Entity APIs
 */
export const accounts = createCrudApi('accounts');
export const leads = createCrudApi('leads');
export const contacts = createCrudApi('contacts');
export const opportunities = createCrudApi('opportunity');
export const tickets = createCrudApi('cases');
export const tasks = createCrudApi('tasks');
export const events = createCrudApi('events');
export const invoices = createCrudApi('invoices');

/**
 * Export all as default
 */
export default {
  auth,
  accounts,
  leads,
  contacts,
  opportunities,
  tickets,
  tasks,
  events,
  invoices,
  apiRequest,
  getAccessToken,
  getRefreshToken,
  getOrgId,
  setOrgId,
  getCurrentUser,
  clearAuthData
};
