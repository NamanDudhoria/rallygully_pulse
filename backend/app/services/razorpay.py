"""Razorpay: online payment for RG Store sales (and later bookings). NOT WIRED IN YET.

Today a store sale paid through Razorpay is recorded by the Venue Manager choosing "Razorpay" as the method once
the customer has paid (for example on a Razorpay QR or payment link created outside Pulse). The outline below is
how Pulse will create the payment itself and confirm the sale only when Razorpay says it was paid:

  1. The VM builds the cart and picks "Razorpay"; Pulse records the sale as `pending` (stock reserved) and asks
     Razorpay for a payment link for the total, sent to the customer's phone.
  2. Razorpay calls our webhook when the customer pays. Pulse checks the signature and marks the sale
     `confirmed`. If the link expires unpaid, the sale is voided and the stock goes back.

To turn it on: set PULSE_RAZORPAY_KEY_ID, PULSE_RAZORPAY_KEY_SECRET and PULSE_RAZORPAY_WEBHOOK_SECRET (see
app/config.py), uncomment this module and the webhook route in app/routers/store.py, and add a `pending` state to
the sale flow in app/services/store.py. Payment links need the account to be activated for live payments.
"""
# import base64
# import hashlib
# import hmac
# import json
# import urllib.request
#
# from .. import config
#
# API = "https://api.razorpay.com/v1"
#
#
# def _auth() -> str:
#     return "Basic " + base64.b64encode(f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()).decode()
#
#
# def create_payment_link(sale_id: int, amount_rupees: float, phone: str, name: str | None, description: str) -> dict:
#     """Ask Razorpay for a payment link; returns {"id": "plink_...", "short_url": "https://rzp.io/..."}."""
#     body = json.dumps({
#         "amount": int(round(amount_rupees * 100)),          # paise
#         "currency": "INR",
#         "reference_id": f"store-{sale_id}",
#         "description": description[:2048],
#         "customer": {"contact": f"+91{phone}", "name": name or ""},
#         "notify": {"sms": True, "email": False},
#         "reminder_enable": True,
#         "expire_by": None,                                  # set a unix time to auto-expire
#     }).encode()
#     req = urllib.request.Request(f"{API}/payment_links", data=body, method="POST",
#                                  headers={"Authorization": _auth(), "Content-Type": "application/json"})
#     with urllib.request.urlopen(req, timeout=15) as resp:
#         return json.loads(resp.read())
#
#
# def verify_webhook(raw_body: bytes, signature: str) -> bool:
#     """Razorpay signs each webhook with the webhook secret (HMAC-SHA256 of the raw body)."""
#     expected = hmac.new(config.RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
#     return hmac.compare_digest(expected, signature or "")
