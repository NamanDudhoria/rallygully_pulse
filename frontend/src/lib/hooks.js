import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

/**
 * Fetch JSON for `path` (null = skip). Keeps the previous data visible while a new
 * path loads; `reload()` returns a promise for callers that refresh after a mutation.
 */
export function useApi(path, { interval } = {}) {
  const [state, setState] = useState({ path: null, data: null, error: null })
  const fetchNow = useCallback((signal) => {
    if (!path) return Promise.resolve()
    return api(path, { signal })
      .then((data) => setState({ path, data, error: null }))
      .catch((error) => { if (error.name !== 'AbortError') setState((s) => ({ ...s, path, error })) })
  }, [path])
  useEffect(() => {
    const c = new AbortController()
    fetchNow(c.signal)
    return () => c.abort()
  }, [fetchNow])
  useEffect(() => {
    if (!interval) return undefined
    const id = setInterval(() => { if (document.visibilityState === 'visible') fetchNow() }, interval)
    return () => clearInterval(id)
  }, [interval, fetchNow])
  const reload = useCallback(() => fetchNow(), [fetchNow])
  return { data: state.data, error: state.error, loading: !!path && state.path !== path, reload }
}

export function useLocalState(key, initial) {
  const [v, setV] = useState(() => {
    try { const raw = localStorage.getItem(key); return raw == null ? initial : JSON.parse(raw) } catch { return initial }
  })
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(v)) } catch { /* storage unavailable */ } }, [key, v])
  return [v, setV]
}

/** Comparison baseline shared by Portfolio and venue pages; "custom" carries a date range for the API. */
export function useBaseline() {
  const [win, setWin] = useLocalState('pulse.window', 'same_weekday')
  const [range, setRange] = useLocalState('pulse.cmpRange', null)
  const custom = win === 'custom' && range?.from && range?.to && range.from <= range.to
  const query = custom ? `&cmp_from=${range.from}&cmp_to=${range.to}` : ''
  return { win, setWin, range, setRange, query }
}
