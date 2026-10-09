import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import logo from '../assets/logo.svg'
import { useAuth } from '../lib/useCtx'
import { Field } from '../components/ui'

const DEMO = [
  ['hq@rallygully.com', 'HQ Operations', 'Full portfolio'],
  ['vm.eok@rallygully.com', 'Venue Manager', 'East of Kailash'],
  ['vm.skt@rallygully.com', 'Venue Manager', 'Saket'],
  ['vm.ggn@rallygully.com', 'Venue Manager', 'Gurugram 54'],
]

export default function Login() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

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
        <form className="login-card" onSubmit={submit}>
          <div>
            <h1>Sign in to Pulse</h1>
            <p className="muted" style={{ marginTop: 6 }}>Use your RallyGully work account.</p>
          </div>
          {error && <div className="callout bad" role="alert">{error}</div>}
          <Field label="Email">
            <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </Field>
          <Field label="Password">
            <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </Field>
          <button className="btn btn-primary" disabled={busy} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
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
      </section>
    </div>
  )
}
