const BASE = import.meta.env.VITE_API_URL || ''

let token = (() => {
  try { return localStorage.getItem('pulse.token') } catch { return null }
})()

export const hasToken = () => !!token

export function setToken(t) {
  token = t
  try { t ? localStorage.setItem('pulse.token', t) : localStorage.removeItem('pulse.token') } catch { /* private mode */ }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

export async function api(path, { method = 'GET', body, signal } = {}) {
  const res = await fetch(BASE + path, {
    method,
    signal,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && token) {
    setToken(null)
    window.dispatchEvent(new Event('pulse:logout'))
  }
  const data = res.headers.get('content-type')?.includes('json') ? await res.json() : null
  if (!res.ok) {
    const detail = data?.detail
    const msg = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join('; ')
      : `Request failed (${res.status})`
    throw new ApiError(res.status, msg)
  }
  return data
}

export const post = (path, body = {}) => api(path, { method: 'POST', body })
export const patch = (path, body = {}) => api(path, { method: 'PATCH', body })
export const put = (path, body = {}) => api(path, { method: 'PUT', body })
export const del = (path) => api(path, { method: 'DELETE' })
