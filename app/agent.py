"""
CheckoutAgent.checkout() is the single entry point every buyer (human or AI)
goes through. It never skips a step and never calls razorpay_client directly
without first logging the policy decision that authorised the call.

Flow per request:
  1. resolve buyer's requested items against the catalog        -> audit: cart_build
  2. run every policy check against the cart                    -> audit: policy_check
     -> if blocked: stop here, explain, no money action at all
  3. offer at most one bounded upsell, buyer-agent may accept    -> audit: upsell_offer
  4. if total >= human-confirmation threshold and not confirmed: -> audit: confirmation_required
     stop and ask, no money action taken
  5. optional: run independent LLM auditor for oversight         -> audit: llm_audit_review
  6. call Razorpay to create the order, catch + explain failures -> audit: razorpay_order_create / razorpay_failure
  7. return a structured, buyer-facing result                    -> audit: checkout_result
"""
import uuid
from typing import Optional

from . import catalog, policy, config
from . import upsell as upsell_mod
from .audit import AuditTrail
from .razorpay_client import client, RazorpayError

_SESSION_SPEND = {}  # session_id -> paise spent so far (in-memory demo state)
_SESSION_HISTORY = {}  # session_id -> list of previous order details for audit context


class CheckoutAgent:
    def __init__(self, audit: Optional[AuditTrail] = None):
        self.audit = audit or AuditTrail()

    def _session_spend(self, session_id: str) -> int:
        return _SESSION_SPEND.get(session_id, 0)

    def _record_spend(self, session_id: str, amount_paise: int, order_details: dict = None):
        _SESSION_SPEND[session_id] = self._session_spend(session_id) + amount_paise
        if order_details:
            if session_id not in _SESSION_HISTORY:
                _SESSION_HISTORY[session_id] = []
            _SESSION_HISTORY[session_id].append(order_details)

    def checkout(
        self,
        session_id: str,
        items: list,                      # [{"product_id": ..., "qty": ...}, ...]
        accept_upsell: bool = True,
        buyer_confirmed_high_value: bool = False,
        with_auditor: bool = False,
        customer_goal: str = "",
        groq_api_key: str = "",
    ) -> dict:
        # 1. Resolve cart
        cart = []
        unresolved = []
        for it in items:
            product = catalog.find_product(it["product_id"])
            if not product:
                unresolved.append(it["product_id"])
                continue
            qty = max(1, int(it.get("qty", 1)))
            cart.append({"product": product, "qty": qty, "line_total_paise": product["price_paise"] * qty})

        self.audit.log(
            session_id, "cart_build",
            summary=f"Resolved {len(cart)}/{len(items)} requested line(s); {len(unresolved)} unknown SKU(s).",
            inputs={"requested": items, "unresolved_ids": unresolved},
            outcome="info" if not unresolved else "failure",
            explanation=(f"Could not find product id(s) {unresolved} in the catalog." if unresolved else ""),
        )
        if not cart:
            return self._result(session_id, False, "No valid items to check out.", cart=[], order=None)

        # 2. Policy gate
        checks = policy.evaluate_cart(cart, self._session_spend(session_id), set())
        passed = policy.all_passed(checks)
        self.audit.log(
            session_id, "policy_check",
            summary=f"{sum(c.passed for c in checks)}/{len(checks)} checks passed.",
            inputs={"cart": [{"id": i["product"]["id"], "qty": i["qty"]} for i in cart]},
            policy_checks=[c.as_dict() for c in checks],
            outcome="allowed" if passed else "blocked",
            explanation="; ".join(c.detail for c in checks if not c.passed) or "All bounds satisfied.",
        )
        if not passed:
            failing = [c for c in checks if not c.passed]
            msg = ("This order is blocked by merchant policy before any charge was attempted: "
                   + "; ".join(f"{c.rule} ({c.detail})" for c in failing))
            return self._result(session_id, False, msg, cart=cart, order=None)

        # 3. Bounded upsell (offer only, never auto-charges beyond what's accepted)
        candidate, reason = upsell_mod.suggest(cart, self._session_spend(session_id))
        upsell_added = None
        if candidate:
            self.audit.log(
                session_id, "upsell_offer",
                summary=f"Offered '{candidate['name']}'.",
                inputs={"candidate_id": candidate["id"]},
                outcome="info",
                explanation=reason,
            )
            if accept_upsell:
                cart.append({"product": candidate, "qty": 1, "line_total_paise": candidate["price_paise"]})
                upsell_added = candidate["id"]
                # re-run policy on the upsell-inclusive cart -- an accepted upsell
                # must clear the same gate as everything else, no exceptions.
                checks2 = policy.evaluate_cart(cart, self._session_spend(session_id), set())
                if not policy.all_passed(checks2):
                    cart.pop()  # revert -- upsell must never push the order past a bound
                    upsell_added = None
                    self.audit.log(
                        session_id, "upsell_reverted",
                        summary="Upsell would have breached policy after acceptance; reverted.",
                        policy_checks=[c.as_dict() for c in checks2],
                        outcome="blocked",
                        explanation="Accepted upsell re-checked against policy and failed, so it was dropped rather than charged.",
                    )

        order_total = sum(i["line_total_paise"] for i in cart)

        # 4. Optional LLM auditor for independent oversight
        audit_result = None
        if with_auditor:
            try:
                from . import auditor
                from . import catalog as catalog_module
                
                audit_result = auditor.audit_decision(
                    goal=customer_goal,
                    cart=cart,
                    catalog=catalog_module.get_manifest(),
                    session_spend_so_far_paise=self._session_spend(session_id),
                    session_history=_SESSION_HISTORY.get(session_id, []),
                    groq_api_key=groq_api_key,
                )
                
                self.audit.log(
                    session_id, "llm_audit_review",
                    summary=f"Independent auditor: {audit_result['risk_flag'].upper()} - {audit_result['reasoning']}",
                    inputs={"customer_goal": customer_goal, "cart_items": len(cart)},
                    outcome="flagged" if audit_result["risk_flag"] == "flagged_for_review" else "clean",
                    explanation=f"Using Groq model {audit_result['model_used']} for independent oversight. "
                                f"Risk assessment: {audit_result['risk_flag']}",
                )
            except Exception as e:
                # Auditor failure should not block checkout - log and continue
                self.audit.log(
                    session_id, "llm_audit_review",
                    summary=f"Auditor call failed: {str(e)}",
                    outcome="failure",
                    explanation="Independent LLM auditor failed but checkout continues.",
                )

        # 5. Human-confirmation gate for high-value orders
        if policy.requires_human_confirmation(cart) and not buyer_confirmed_high_value:
            self.audit.log(
                session_id, "confirmation_required",
                summary=f"INR {order_total/100:.2f} requires explicit buyer confirmation before charging.",
                outcome="blocked",
                explanation="No Razorpay call made. Awaiting buyer_confirmed_high_value=true on retry.",
            )
            return self._result(
                session_id, False,
                f"This order totals INR {order_total/100:.2f}, above the INR {config.HUMAN_CONFIRM_ABOVE_PAISE/100:.2f} "
                "auto-approval line. Please confirm explicitly to proceed -- no charge has been made.",
                cart=cart, order=None, needs_confirmation=True,
            )

        # 6. Razorpay call, explicit failure handling
        receipt = f"rcpt_{session_id}_{uuid.uuid4().hex[:10]}"
        try:
            order = client.create_order(
                amount_paise=order_total,
                currency="INR",
                receipt=receipt,
                notes={"session_id": session_id, "skus": ",".join(i["product"]["id"] for i in cart)},
            )
            order_details = {
                "order_id": order.id,
                "amount": order_total,
                "items": [{"id": i["product"]["id"], "qty": i["qty"]} for i in cart]
            }
            self._record_spend(session_id, order_total, order_details)
            self.audit.log(
                session_id, "razorpay_order_create",
                summary=f"Order {order.id} created for INR {order_total/100:.2f}.",
                razorpay={"amount": order.amount, "currency": order.currency, "receipt": order.receipt, "order_id": order.id},
                outcome="success",
                explanation=f"Charge authorised only after policy_check=allowed and (if required) buyer confirmation.",
            )
            return self._result(
                session_id, True,
                f"Order created: {order.id} for INR {order_total/100:.2f}.",
                cart=cart, order=order.__dict__, upsell_added=upsell_added, audit_result=audit_result,
            )
        except RazorpayError as e:
            # --- the one gracefully-handled failure path ---
            self.audit.log(
                session_id, "razorpay_failure",
                summary=f"Gateway rejected order: {e.code}",
                razorpay={"error_code": e.code, "error_description": e.description, "attempted_amount": order_total},
                outcome="failure",
                explanation=e.description,
            )
            remediation = self._remediate(e, cart)
            result = self._result(
                session_id, False,
                f"Payment could not be created ({e.code}): {e.description}",
                cart=cart, order=None,
            )
            result["remediation"] = remediation
            self.audit.log(
                session_id, "checkout_result",
                summary="Checkout failed but recovered gracefully with a remediation offer.",
                outcome="failure",
                explanation=remediation,
            )
            return result

    def _remediate(self, e: RazorpayError, cart: list) -> str:
        if e.code == "GATEWAY_AMOUNT_LIMIT_EXCEEDED":
            half = []
            total = sum(i["line_total_paise"] for i in cart)
            return (f"This single order (INR {total/100:.2f}) exceeds what the gateway allows in one charge. "
                    f"Suggest splitting into multiple orders (e.g. by line item, or in two batches) and "
                    f"retrying each with a fresh receipt id -- no funds were moved.")
        return "Suggest retrying with a corrected amount/currency, or contacting merchant support -- no funds were moved."

    def _result(self, session_id, success, message, cart, order, upsell_added=None, needs_confirmation=False, audit_result=None):
        return {
            "session_id": session_id,
            "success": success,
            "message": message,
            "cart": [{"id": i["product"]["id"], "name": i["product"]["name"], "qty": i["qty"],
                      "line_total_paise": i["line_total_paise"]} for i in cart],
            "order_total_paise": sum(i["line_total_paise"] for i in cart),
            "order": order,
            "upsell_added": upsell_added,
            "needs_confirmation": needs_confirmation,
            "audit_result": audit_result,
        }
