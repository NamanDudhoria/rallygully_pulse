import { useState } from 'react'
import { post } from '../lib/api'
import { ArrowRight } from 'lucide-react'
import logo from '../assets/logo.svg'
import { useAuth } from '../lib/useCtx'
import { Field } from '../components/ui'

const DEMO = [
  ['admin@rallygully.com', 'HQ Admin', 'Everything + people'],
  ['hq@rallygully.com', 'HQ', 'Operations, config, roster'],
  ['community@rallygully.com', 'Community Ops', 'Games + analytics'],
  ['academy@rallygully.com', 'Academy', 'Academy + analytics'],
  ['vm.eok@rallygully.com', 'Venue Manager', 'Rostered at EOK today'],
  ['vm.eok.b@rallygully.com', 'Venue Manager', 'Weekend shifts only'],
]

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
      <section className="login-art" aria-hidden>
        <img src={logo} alt="" style={{ height: 30, filter: 'brightness(0) invert(1)', alignSelf: 'flex-start' }} />
        <svg className="pulse-svg" viewBox="0 0 520 120" fill="none">
          <path d="M0 60h150l20-40 30 80 26-60 18 20h276" stroke="#f28e22" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <div>
          <h1>What actually happened at every venue — already known.</h1>
          <p>Pulse is RallyGully&apos;s source of truth for venue inventory, on-ground activity, payments and performance.</p>
        </div>
        <span style={{ fontSize: 12, opacity: 0.6 }}>Internal tool · RallyGully HQ &amp; Venue Managers only</span>
      </section>
      <section className="login-form">
        {mode !== 'login' ? (
          <form className="login-card" onSubmit={async (e) => {
            e.preventDefault()
            setBusy(true); setError(null)
            try { await post('/api/auth/forgot', { email }); setMode('sent') } catch (err) { setError(err.message) } finally { setBusy(false) }
          }}>
            <div>
              <h1>Reset your password</h1>
              <p className="muted" style={{ marginTop: 6 }}>
                {mode === 'sent' ? 'If that email has a Pulse account, a reset link is on its way. It expires in 1 hour.'
                  : "Enter your work email and we'll send a link to set a new password."}
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
            <h1>Sign in to Pulse</h1>
            <p className="muted" style={{ marginTop: 6 }}>Use your RallyGully work email. Accounts are created by HQ.</p>
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
          <div className="divider" />
          <div>
            <div className="section-title">Demo accounts · password pulse1234</div>
            <div className="demo-logins">
              {DEMO.map(([e, role, scope]) => (
                <button key={e} type="button" className="btn" disabled={busy} onClick={() => submit(null, [e, 'pulse1234'])}>
                  <span style={{ textAlign: 'left' }}><b style={{ fontWeight: 600 }}>{role}</b> <span className="subtle">· {scope}</span></span>
                  <ArrowRight aria-hidden />
                </button>
              ))}
            </div>
          </div>
        </form>
        )}
      </section>
    </div>
  )
}
