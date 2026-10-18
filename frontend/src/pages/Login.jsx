import { useState } from 'react'
import { post } from '../lib/api'
import logo from '../assets/logo.svg'
import { useAuth } from '../lib/useCtx'
import { Field } from '../components/ui'


export default function Login() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState('login') // login | forgot | sent

  const submit = async (e, creds) => {
    e?.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(creds?.[0] ?? email, creds?.[1] ?? password)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <section className="login-form">
        {mode !== 'login' ? (
          <form className="login-card" onSubmit={async (e) => {
            e.preventDefault()
            setBusy(true); setError(null)
            try { await post('/api/auth/forgot', { email }); setMode('sent') } catch (err) { setError(err.message) } finally { setBusy(false) }
          }}>
            <div>
              <img src={logo} alt="RallyGully" style={{ height: 26, marginBottom: 16 }} />
              <h1>Reset password</h1>
              <p className="muted" style={{ marginTop: 6 }}>
                {mode === 'sent' ? 'If that email has a Pulse account, a reset link is on its way. It expires in 1 hour.'
                  : 'We’ll email you a reset link.'}
              </p>
            </div>
            {error && <div className="callout bad" role="alert">{error}</div>}
            {mode === 'forgot' && (
              <>
                <Field label="Work email">
                  <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
                </Field>
                <button className="btn btn-primary" disabled={busy} type="submit">{busy ? 'Sending…' : 'Send reset link'}</button>
              </>
            )}
            <button type="button" className="btn btn-ghost" onClick={() => { setMode('login'); setError(null) }}>Back to sign in</button>
          </form>
        ) : (
        <form className="login-card" onSubmit={submit}>
          <div>
            <img src={logo} alt="RallyGully" style={{ height: 26, marginBottom: 16 }} />
            <h1>Sign in</h1>
          </div>
          {error && <div className="callout bad" role="alert">{error}</div>}
          <Field label="Email">
            <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </Field>
          <Field label="Password">
            <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </Field>
          <button className="btn btn-primary" disabled={busy} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
          <button type="button" className="btn btn-ghost btn-sm" style={{ justifySelf: 'start' }} onClick={() => { setMode('forgot'); setError(null) }}>Forgot password?</button>
        </form>
        )}
      </section>
    </div>
  )
}
