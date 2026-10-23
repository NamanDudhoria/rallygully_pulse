/** Helpers for VM clock-in / clock-out. */
import { time12 } from './format'

/** "2026-10-07T17:05:00" → "5:05 PM" (server times are venue-local). */
export const clockTime = (iso) => (iso ? time12(Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16))) : '')

/** The phone's current position, asking for GPS precision. */
export function getFix() {
  return new Promise((resolve, reject) => {
    if (!window.isSecureContext) {
      reject(new Error('Location needs the secure (https) Pulse address. Open Pulse from its https link.'))
      return
    }
    if (!('geolocation' in navigator)) {
      reject(new Error("This browser can't share your location. Use Chrome or Safari on your phone."))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy }),
      (e) => reject(new Error(
        e.code === 1 ? 'Location permission is off for Pulse. Allow location for this site in your browser settings, then try again.'
          : e.code === 3 ? 'Finding your location took too long. Check that GPS / location is on and try again.'
            : "Couldn't find your location. Check that GPS / location is on and try again.")),
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 })
  })
}
