import { useCallback, useContext, useState } from 'react'
import { AuthCtx, ToastCtx } from './contexts'

export const useAuth = () => useContext(AuthCtx)
export const useToast = () => useContext(ToastCtx)

/** Run an API mutation with toast feedback. Returns the result, or undefined on error. */
export function useAction() {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const run = useCallback(async (fn, success) => {
    setBusy(true)
    try {
      const r = await fn()
      if (success) toast(typeof success === 'function' ? success(r) : success)
      return r
    } catch (e) {
      toast(e.message, 'error')
      return undefined
    } finally {
      setBusy(false)
    }
  }, [toast])
  return [run, busy]
}
