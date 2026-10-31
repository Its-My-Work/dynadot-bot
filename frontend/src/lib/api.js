/** Typed-ish API client for the FastAPI backend.
 *
 * Two rules the whole app relies on:
 *
 * 1. **Language goes out on every request.** The backend localises error
 *    `detail` strings, so an `X-Language` header is cheaper and more reliable
 *    than translating server errors again on the client.
 * 2. **Errors become `ApiError`, never raw rejections.** Every caller needs the
 *    HTTP status (409 = domain taken, 503 = registrar down) to pick the right
 *    message, so the status is preserved rather than flattened into a string.
 */

const BASE = import.meta.env?.VITE_API_BASE ?? '/api/v1';

export class ApiError extends Error {
  constructor(message, { status = 0, detail = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }

  /** Did we fail because the domain was taken while the user was deciding? */
  get isConflict() {
    return this.status === 409;
  }

  /** Registrar or gateway temporarily unavailable — worth a retry button. */
  get isUnavailable() {
    return this.status === 503;
  }
}

let currentLanguage = 'ru';

export function setLanguage(language) {
  currentLanguage = language === 'en' ? 'en' : 'ru';
}

function buildUrl(path, params) {
  const url = `${BASE}${path}`;
  if (!params) return url;
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.append(key, String(value));
    }
  });
  const qs = search.toString();
  return qs ? `${url}?${qs}` : url;
}

let adminToken = null;

export function getAdminToken() {
  if (typeof localStorage !== 'undefined') {
    try {
      return localStorage.getItem('dynadot_admin_token');
    } catch {
      // Ignored
    }
  }
  return adminToken;
}

export function setAdminToken(token) {
  adminToken = token;
  try {
    if (token) {
      localStorage.setItem('dynadot_admin_token', token);
    } else {
      localStorage.removeItem('dynadot_admin_token');
    }
  } catch {
    // Best-effort storage
  }
}

async function request(path, { method = 'GET', body, params, signal, headers: extraHeaders = {} } = {}) {
  const headers = { 'X-Language': currentLanguage, ...extraHeaders };
  if (adminToken && path.startsWith('/admin') && !headers['Authorization']) {
    headers['Authorization'] = `Basic ${adminToken}`;
  }
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers,
      signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    // AbortError is a deliberate cancellation (the user typed again), not a
    // failure worth surfacing — callers check the name.
    if (cause?.name === 'AbortError') throw cause;
    throw new ApiError('network_error', { status: 0 });
  }

  if (response.status === 204) return null;

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }

  if (!response.ok) {
    const detail =
      typeof payload?.detail === 'string'
        ? payload.detail
        : (payload?.detail?.[0]?.msg ?? null);
    throw new ApiError(detail || `http_${response.status}`, {
      status: response.status,
      detail,
    });
  }

  return payload;
}

export const api = {
  // --- meta ---------------------------------------------------------------
  health: () => request('/health'),
  languages: () => request('/languages'),

  // --- users --------------------------------------------------------------
  getUser: (telegramId) => request(`/users/${telegramId}`),
  ensureUser: (telegramId) =>
    request('/users/register', { method: 'POST', params: { telegram_id: telegramId } }),
  setUserLanguage: (telegramId, language) =>
    request(`/users/${telegramId}/language`, {
      method: 'PATCH',
      body: { language },
    }),
  setUserTheme: (telegramId, theme) =>
    request(`/users/${telegramId}/theme`, {
      method: 'PATCH',
      body: { theme },
    }),

  // --- search -------------------------------------------------------------
  zones: () => request('/domains/zones'),
  catalog: () => request('/domains/catalog'),
  search: (query, tlds, signal, options = {}) =>
    request('/domains/search', {
      method: 'POST',
      body: {
        query,
        tlds: tlds?.length ? tlds : null,
        category: options.category || null,
        offset: options.offset || 0,
        limit: options.limit || 15,
      },
      signal,
    }),
  aiSuggest: (query, signal) =>
    request('/domains/ai-suggest', {
      method: 'POST',
      body: { query },
      signal,
    }),

  // --- domains ------------------------------------------------------------
  myDomains: (telegramId) => request('/domains/mine', { params: { telegram_id: telegramId } }),
  domainDetails: (domainId) => request(`/domains/${domainId}/details`),
  updateDns: (domainId, records) =>
    request(`/domains/${domainId}/dns`, {
      method: 'PUT',
      body: { records },
    }),
  applyQuickPreset: (domainId, presetType, target) =>
    request(`/domains/${domainId}/quick-preset`, {
      method: 'POST',
      body: { preset_type: presetType, target },
    }),
  togglePrivacy: (domainId, enabled) =>
    request(`/domains/${domainId}/privacy`, {
      method: 'PUT',
      body: { enabled },
    }),
  toggleLock: (domainId, locked) =>
    request(`/domains/${domainId}/lock`, {
      method: 'PUT',
      body: { locked },
    }),
  renewDomain: (domainId, telegramId, years = 1) =>
    request(`/domains/${domainId}/renew`, {
      method: 'POST',
      body: { years },
      params: { telegram_id: telegramId },
    }),
  setNameservers: (domainId, nameservers) =>
    request(`/domains/${domainId}/nameservers`, {
      method: 'PUT',
      body: { nameservers },
    }),

  // --- contacts -----------------------------------------------------------
  contacts: (telegramId) => request('/contacts', { params: { telegram_id: telegramId } }),
  createContact: (telegramId, contact) =>
    request('/contacts', {
      method: 'POST',
      params: { telegram_id: telegramId },
      body: contact,
    }),

  // --- orders -------------------------------------------------------------
  createOrder: (telegramId, order) =>
    request('/orders', {
      method: 'POST',
      params: { telegram_id: telegramId },
      body: order,
    }),
  orders: (telegramId) => request('/orders', { params: { telegram_id: telegramId } }),
  order: (orderId) => request(`/orders/${orderId}`),
  cancelOrder: (orderId) => request(`/orders/${orderId}/cancel`, { method: 'POST' }),

  // --- payments -----------------------------------------------------------
  checkout: (orderId) =>
    request('/payments/checkout', { method: 'POST', body: { order_id: orderId } }),
  payment: (paymentId) => request(`/payments/${paymentId}`),
  refreshPayment: (paymentId) =>
    request(`/payments/${paymentId}/refresh`, { method: 'POST' }),

  // --- admin --------------------------------------------------------------
  adminLogin: async (username, password) => {
    const token = btoa(`${username}:${password}`);
    const res = await request('/admin/login', {
      method: 'POST',
      body: { username, password },
    });
    setAdminToken(token);
    return res;
  },
  adminLogout: () => {
    setAdminToken(null);
  },
  adminStats: () => request('/admin/stats'),
  adminOrders: (params) => request('/admin/orders', { params }),
  adminRefunds: () => request('/admin/refunds'),
  resolveRefund: (orderId, note) =>
    request(`/admin/refunds/${orderId}/resolve`, { method: 'POST', body: { note } }),
  adminTasks: (params) => request('/admin/tasks', { params }),
  retryTask: (taskId) => request(`/admin/tasks/${taskId}/retry`, { method: 'POST' }),
  adminPricing: () => request('/admin/tld-pricing'),
  createPricing: (data) =>
    request('/admin/tld-pricing', { method: 'POST', body: data }),
  updatePricing: (tld, patch) =>
    request(`/admin/tld-pricing/${tld}`, { method: 'PATCH', body: patch }),
  deletePricing: (tld) =>
    request(`/admin/tld-pricing/${tld}`, { method: 'DELETE' }),
  syncTldPrices: () => request('/admin/sync-tld-prices', { method: 'POST' }),
  adminApiLogs: (params) => request('/admin/api-logs', { params }),
  queueStatus: () => request('/admin/queue-status'),
  adminSettings: () => request('/admin/settings'),
  updateAdminSettings: (data) =>
    request('/admin/settings', { method: 'PATCH', body: data }),
  testConnection: (provider) =>
    request(`/admin/test-connection/${provider}`, { method: 'POST' }),

  // --- vps & vpn -----------------------------------------------------------
  vpsLocations: () => request('/servers/locations'),
  vpsPresets: () => request('/servers/presets'),
  createServerOrder: (telegramId, data) =>
    request(`/servers/orders?telegram_id=${telegramId}`, {
      method: 'POST',
      body: data,
    }),
  myServers: (telegramId) => request(`/servers/my?telegram_id=${telegramId}`),
  serverDetails: (telegramId, serverId) =>
    request(`/servers/${serverId}?telegram_id=${telegramId}`),
  deleteServer: (telegramId, serverId) =>
    request(`/servers/${serverId}?telegram_id=${telegramId}`, {
      method: 'DELETE',
    }),
};

export default api;
