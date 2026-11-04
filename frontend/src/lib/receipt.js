import { apiBlobUrl } from './api'

/** Open a receipt photo in a new tab. The photo needs the sign-in token, so it is fetched first. */
export async function openReceipt(id) {
  const url = await apiBlobUrl(`/api/vm-cash/photo/${id}`)
  window.open(url, '_blank', 'noopener')
}
