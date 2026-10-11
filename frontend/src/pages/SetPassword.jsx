import { useEffect, useState } from 'react'
import { KeyRound } from 'lucide-react'
import logo from '../assets/logo.svg'
import { api, post } from '../lib/api'
import { useAuth } from '../lib/useCtx'
import { Field } from '../components/ui'

/** Landing page for invite and password-reset links: /set-password?token=… */
export default function SetPassword() {
  const { adopt } = useAuth()
  const token = new URLSearchParams(window.location.search).get('token') || ''
  const [info, setInfo] = useState(null)
  const [pw, setPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api(`/api/auth/token?token=${encodeURIComponent(token)}`).then(setInfo).catch(() => setInfo({ valid: false })) }, [token])
  const mismatch = confirm && confirm !== pw
  const go = () => { window.history.replaceState(null, '', '/'); window.dispatchEvent(new PopStateEvent('popstate')) }

  return (
    <div className="login-form" style={{ minHeight: '100vh' }}>
      <form className="login-card card card-pad" onSubmit={async (e) => {
        e.preventDefault()
        setBusy(true); setError(null)
        try {
          adopt(await post('/api/auth/set-password', { token, password: pw }))
          go()
        } catch (err) { setError(err.message) } finally { setBusy(false) }
      }}>
        <img src={logo} alt="RallyGully" style={{ height: 22, justifySelf: 'start' }} />
        {!info ? <p className="subtle">Checking your link…</p> : !info.valid ? (
          <>
            <h1>Link expired</h1>
            <p className="muted">This link is invalid, already used, or expired. Invite links last 72 hours and reset links 1 hour.
              Ask HQ to resend it, or use “Forgot password?” on the sign-in page.</p>
            <button type="button" className="btn btn-primary" onClick={go}>Go to sign in</button>
          </>
        ) : (
          <>
            <div>
              <h1>{info.purpose === 'invite' ? `Welcome, ${info.name}` : 'Set a new password'}</h1>
              <p className="muted" style={{ marginTop: 6 }}>{info.purpose === 'invite' ? 'Choose a password to activate your Pulse account' : 'Choose a new password'} for <b>{info.email}</b>.</p>
            </div>
            {error && <div className="callout bad" role="alert">{error}</div>}
            <Field label="New password" help="At least 8 characters. A short phrase works well.">
              <input className="input" type="password" autoComplete="new-password" minLength={8} value={pw} onChange={(e) => setPw(e.target.value)} required autoFocus />
            </Field>
            <Field label="Confirm password" error={mismatch ? "Passwords don't match" : null}>
              <input className="input" type="password" autoComplete="new-password" value={confirm} aria-invalid={!!mismatch} onChange={(e) => setConfirm(e.target.value)} required />
            </Field>
            <button className="btn btn-primary" disabled={busy || mismatch}><KeyRound /> {busy ? 'Saving…' : 'Set password & sign in'}</button>
          </>
        )}
      </form>
    </div>
  )
}
