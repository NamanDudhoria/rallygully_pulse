import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, AlertCircle } from 'lucide-react'
import { api, hasToken, post, setToken } from './api'
import { AuthCtx, ToastCtx } from './contexts'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined) // undefined = checking
  useEffect(() => {
    if (hasToken()) api('/api/auth/me').then(setUser).catch(() => setUser(null))
    else Promise.resolve().then(() => setUser(null))
    const out = () => setUser(null)
    window.addEventListener('pulse:logout', out)
    return () => window.removeEventListener('pulse:logout', out)
  }, [])
  const login = useCallback(async (email, password) => {
    const r = await post('/api/auth/login', { email, password })
    setToken(r.token)
    setUser(r.user)
    return r.user
  }, [])
  const logout = useCallback(() => { setToken(null); setUser(null) }, [])
  const value = useMemo(() => ({ user, login, logout, isHQ: user?.role === 'hq' }), [user, login, logout])
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const push = useCallback((message, kind = 'ok') => {
    const id = Math.random().toString(36).slice(2)
    setToasts((t) => [...t.slice(-2), { id, message, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'error' ? 6000 : 3200)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.kind === 'error' ? <AlertCircle aria-hidden /> : <CheckCircle2 aria-hidden />}
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
