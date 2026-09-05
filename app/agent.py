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
import json
import os
import uuid
from typing import Optional

from . import catalog, policy, config
from . import upsell as upsell_mod
from .audit import AuditTrail
from .razorpay_client import client, RazorpayError


def get_ai_buyer_cart(goal: str, catalog_data: dict, gemini_api_key: str = "") -> tuple[list, str, str]:
    """
    Get cart selection from AI buyer using Gemini API, with fallback to mock mode.
    
    Returns:
        tuple: (cart_items, mode_used, error_message) where mode_used is "gemini" or "mock"
    """
    effective_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    # Try Gemini API if key is provided or in environment
    if effective_key:
        try:
            import google.generativeai as genai
            
            genai.configure(api_key=effective_key)
            
            system_prompt = """You are an AI buyer agent. Your task is to choose products from a merchant catalog to fulfill a shopping goal.

You must respond with ONLY a JSON array of objects, each with:
- "product_id": the exact product ID from the catalog
- "qty": a positive integer quantity

Do NOT include any prose, explanations, or markdown formatting. Just the raw JSON array.

Constraints:
- Only use product_ids that exist in the provided catalog
- Do NOT invent products or IDs
- Choose reasonable quantities based on the goal
- Consider the product descriptions to make appropriate choices"""

            user_message = f"""Here is the merchant catalog:
{json.dumps(catalog_data, indent=2)}

Shopping goal: {goal}

Choose appropriate products and quantities to fulfill this goal."""

            # Create the model and generate content
            # Allow environment variable override
            model_name = os.getenv("GEMINI_MODEL", config.GEMINI_MODEL)
            try:
                model = genai.GenerativeModel(model_name, system_instruction=system_prompt)
                response = model.generate_content(user_message)
            except Exception as model_err:
                fallback_models = ["gemini-flash-latest", "gemini-3.6-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
                response = None
                for fm in fallback_models:
                    if fm != model_name:
                        try:
                            model = genai.GenerativeModel(fm, system_instruction=system_prompt)
                            response = model.generate_content(user_message)
                            model_name = fm
                            break
                        except Exception:
                            continue
                if not response:
                    raise model_err

            llm_response = response.text
            
            # Clean up response if it has markdown code blocks
            cleaned = llm_response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            cleaned = cleaned.strip()
            
            cart = json.loads(cleaned)
            
            # Validate structure
            if not isinstance(cart, list):
                raise ValueError("Response is not a JSON array")
            for item in cart:
                if not isinstance(item, dict):
                    raise ValueError("Cart item is not an object")
                if "product_id" not in item or "qty" not in item:
                    raise ValueError("Cart item missing product_id or qty")
                if not isinstance(item["qty"], int) or item["qty"] <= 0:
                    raise ValueError("Quantity must be a positive integer")
            
            return cart, "gemini", ""
            
        except Exception as e:
            # Fall back to mock mode on any Gemini API error
            import traceback
            error_msg = str(e)
            print(f"Gemini API call failed: {error_msg}, falling back to mock mode")
            traceback.print_exc()
            return get_mock_cart(goal, catalog_data), "mock_fallback", error_msg
    
    # Use mock mode if no key provided
    return get_mock_cart(goal, catalog_data), "mock", ""


def get_mock_cart(goal: str, catalog_data: dict) -> list:
    """Generate a mock cart based on the goal."""
    goal_lower = goal.lower()
    
    if "200 sq ft" in goal_lower:
        # 200 sq ft roof needs reasonable quantities
        return [
            {"product_id": "sku_roof_sheet_std", "qty": 8},
            {"product_id": "sku_installation_basic", "qty": 1}
        ]
    elif "small" in goal_lower or "2" in goal or "few" in goal_lower:
        # Small order
        return [
            {"product_id": "sku_roof_sheet_std", "qty": 3}
        ]
    else:
        # Default reasonable order
        return [
            {"product_id": "sku_roof_sheet_std", "qty": 5},
            {"product_id": "sku_installation_basic", "qty": 1}
        ]

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
        gemini_api_key: str = "",
        company: str = "Afra Infra",
    ) -> dict:
        target_company = company or "Afra Infra"

        def log_audit(action_type, summary, **kwargs):
            kwargs.setdefault("company", target_company)
            return self.audit.log(session_id, action_type, summary, **kwargs)

        # 1. Resolve cart (or use AI buyer if no items provided)
        cart = []
        unresolved = []
        ai_mode = "manual"
        
        # If no items provided but customer goal is set, use AI buyer
        ai_error = None
        if not items and customer_goal:
            try:
                ai_items, ai_mode, error_msg = get_ai_buyer_cart(customer_goal, catalog.get_manifest(), gemini_api_key)
                items = ai_items
                ai_error = error_msg if error_msg else None
                
                # Build explanation with error message if fallback occurred
                explanation = "Cart selected by AI buyer: " + ", ".join(f"{i['product_id']} (qty: {i['qty']})" for i in items)
                if error_msg:
                    explanation = f"Gemini API call failed: {error_msg} — using mock fallback. " + explanation
                
                self.audit.log(
                    session_id, "ai_buyer_selection",
                    summary=f"AI buyer selected {len(items)} item(s) using {ai_mode} mode.",
                    inputs={"customer_goal": customer_goal, "mode": ai_mode, "error": error_msg if error_msg else None},
                    outcome="info",
                    explanation=explanation,
                )
            except Exception as e:
                self.audit.log(
                    session_id, "ai_buyer_selection",
                    summary=f"AI buyer selection failed: {str(e)}",
                    outcome="failure",
                    explanation="Falling back to requiring manual item selection.",
                )
                return self._result(session_id, False, "AI buyer failed to select items. Please select products manually.", cart=[], order=None, ai_mode="error", ai_error=str(e))
        
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
            inputs={"requested": items, "unresolved_ids": unresolved, "ai_mode": ai_mode},
            outcome="info" if not unresolved else "failure",
            explanation=(f"Could not find product id(s) {unresolved} in the catalog." if unresolved else ""),
        )
        if not cart:
            return self._result(session_id, False, "No valid items to check out.", cart=[], order=None, ai_mode=ai_mode, ai_error=ai_error)

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
            return self._result(session_id, False, msg, cart=cart, order=None, ai_mode=ai_mode, ai_error=ai_error)

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
                # Auditor failure should not block checkout - record explicit error status so UI never misidentifies as clean
                audit_result = {
                    "risk_flag": "error",
                    "reasoning": f"Independent LLM auditor unavailable: {str(e)}",
                    "model_used": "groq-unavailable",
                    "error": str(e)
                }
                self.audit.log(
                    session_id, "llm_audit_review",
                    summary=f"Auditor call failed: {str(e)}",
                    outcome="failure",
                    explanation=f"Independent LLM auditor call failed: {str(e)}. Checkout proceeded per safety fallback.",
                )

        # 4.5. Hold order if auditor flagged it for review
        if audit_result and audit_result["risk_flag"] == "flagged_for_review":
            self.audit.log(
                session_id, "order_held_for_approval",
                summary=f"Order held due to auditor flag. Requires human approval before Razorpay call.",
                inputs={"auditor_reasoning": audit_result["reasoning"]},
                outcome="blocked",
                explanation=f"Independent auditor flagged this order: {audit_result['reasoning']}. Order held pending human approval.",
            )
            return self._result(
                session_id, False,
                f"Order held for human approval. Auditor flagged: {audit_result['reasoning']}",
                cart=cart, order=None, needs_approval=True, audit_result=audit_result, ai_mode=ai_mode, ai_error=ai_error,
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
                cart=cart, order=None, needs_confirmation=True, audit_result=audit_result, ai_mode=ai_mode, ai_error=ai_error,
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
                cart=cart, order=order.__dict__, upsell_added=upsell_added, audit_result=audit_result, ai_mode=ai_mode, ai_error=ai_error,
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
                cart=cart, order=None, ai_mode=ai_mode, ai_error=ai_error,
            )
            result["remediation"] = remediation
            self.audit.log(
                session_id, "checkout_result",
                summary="Checkout failed but recovered gracefully with a remediation offer.",
                outcome="failure",
                explanation=remediation,
            )
            return result

    def process_held_order(self, session_id: str, cart: list, audit_result: dict, buyer_confirmed_high_value: bool = False) -> dict:
        """
        Process a held order after human approval.
        
        This is called when a human approves an order that was held due to auditor flag.
        It proceeds directly to Razorpay order creation.
        """
        order_total = sum(i["line_total_paise"] for i in cart)
        
        # Log the approval
        self.audit.log(
            session_id, "order_approved",
            summary=f"Order approved by human. Proceeding to Razorpay.",
            inputs={"order_total_paise": order_total},
            outcome="allowed",
            explanation="Human approved the held order, proceeding to payment.",
        )
        
        # Razorpay call
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
                explanation=f"Charge authorised after human approval of held order.",
            )
            return self._result(
                session_id, True,
                f"Order created: {order.id} for INR {order_total/100:.2f}.",
                cart=cart, order=order.__dict__, audit_result=audit_result,
            )
        except RazorpayError as e:
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
                cart=cart, order=None, audit_result=audit_result,
            )
            result["remediation"] = remediation
            return result

    def _remediate(self, e: RazorpayError, cart: list) -> str:
        if e.code == "GATEWAY_AMOUNT_LIMIT_EXCEEDED":
            half = []
            total = sum(i["line_total_paise"] for i in cart)
            return (f"This single order (INR {total/100:.2f}) exceeds what the gateway allows in one charge. "
                    f"Suggest splitting into multiple orders (e.g. by line item, or in two batches) and "
                    f"retrying each with a fresh receipt id -- no funds were moved.")
        return "Suggest retrying with a corrected amount/currency, or contacting merchant support -- no funds were moved."

    def _result(self, session_id, success, message, cart, order, upsell_added=None, needs_confirmation=False, needs_approval=False, audit_result=None, ai_mode=None, ai_error=None):
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
            "needs_approval": needs_approval,
            "audit_result": audit_result,
            "ai_mode": ai_mode,
            "ai_error": ai_error,
        }
