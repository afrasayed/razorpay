"""
Same interface, two backends:

- mock: fully in-memory, deterministic, simulates Razorpay's Order API
  (including a gateway-side amount ceiling) so the whole flow runs with
  zero credentials and zero network access.
- live: thin wrapper over the real `razorpay` Python SDK, pointed at
  **test-mode** keys (rzp_test_...). Swap RAZORPAY_MODE=live and set
  RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET to use it -- agent.py, policy.py
  and audit.py do not change at all.

This is the *only* module allowed to talk to Razorpay (real or simulated).
Nothing else in the codebase constructs a Razorpay order.
"""
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from . import config


class RazorpayError(Exception):
    """Mirrors the shape of razorpay.errors.BadRequestError closely enough
    for the agent's error-handling path to be identical in mock and live mode."""
    def __init__(self, code: str, description: str):
        self.code = code
        self.description = description
        super().__init__(f"[{code}] {description}")


@dataclass
class OrderResult:
    id: str
    amount: int
    currency: str
    status: str
    receipt: str
    notes: dict


class _MockGateway:
    """Deterministic in-memory simulation of the parts of Razorpay's Order
    API this project needs. Idempotent on receipt id, same as the real API's
    idempotency-key behaviour."""

    def __init__(self):
        self._orders = {}

    def create_order(self, amount: int, currency: str, receipt: str, notes: dict) -> OrderResult:
        if receipt in self._orders:
            return self._orders[receipt]  # idempotent replay

        if amount <= 0:
            raise RazorpayError("BAD_REQUEST_ERROR", "Amount must be a positive integer (paise).")
        if currency != "INR":
            raise RazorpayError("BAD_REQUEST_ERROR", f"Unsupported currency '{currency}' in test mode.")
        if amount > config.MOCK_GATEWAY_CEILING_PAISE:
            # This is the *gateway* rejecting it, distinct from our own
            # merchant policy ceiling in policy.py -- the agent must be able
            # to tell these apart when it explains the failure.
            raise RazorpayError(
                "GATEWAY_AMOUNT_LIMIT_EXCEEDED",
                f"Amount {amount} paise exceeds the simulated gateway ceiling of "
                f"{config.MOCK_GATEWAY_CEILING_PAISE} paise for this test account.",
            )

        order = OrderResult(
            id=f"order_MOCK{uuid.uuid4().hex[:14]}",
            amount=amount,
            currency=currency,
            status="created",
            receipt=receipt,
            notes=notes,
        )
        self._orders[receipt] = order
        return order

    def fetch_payment(self, payment_id: str) -> dict:
        return {"id": payment_id, "status": "captured", "method": "upi"}


class RazorpayClientWrapper:
    def __init__(self):
        self.mode = config.RAZORPAY_MODE
        if self.mode == "live":
            import razorpay  # imported lazily so mock mode never needs the package installed with keys
            self._client = razorpay.Client(auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET))
        else:
            self._client = _MockGateway()

    def create_order(self, amount_paise: int, currency: str, receipt: str, notes: dict) -> OrderResult:
        if self.mode == "live":
            try:
                resp = self._client.order.create({
                    "amount": amount_paise,
                    "currency": currency,
                    "receipt": receipt,
                    "notes": notes,
                    "payment_capture": 1,
                })
                return OrderResult(
                    id=resp["id"], amount=resp["amount"], currency=resp["currency"],
                    status=resp["status"], receipt=resp.get("receipt", receipt), notes=resp.get("notes", {}),
                )
            except Exception as e:  # razorpay.errors.BadRequestError et al.
                code = getattr(e, "code", "UNKNOWN_ERROR")
                desc = getattr(e, "description", str(e))
                raise RazorpayError(code, desc)
        else:
            return self._client.create_order(amount_paise, currency, receipt, notes)

    def fetch_payment(self, payment_id: str) -> dict:
        if self.mode == "live":
            return self._client.payment.fetch(payment_id)
        return self._client.fetch_payment(payment_id)

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """Same HMAC-SHA256 scheme Razorpay uses for real webhooks; works
        identically in mock mode against RAZORPAY_WEBHOOK_SECRET so the
        verification code path is exercised even with no live account."""
        if not config.RAZORPAY_WEBHOOK_SECRET:
            return False
        expected = hmac.new(
            config.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


# module-level singleton so mock in-memory state persists across a demo run
client = RazorpayClientWrapper()
