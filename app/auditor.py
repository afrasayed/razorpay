"""
Independent LLM Auditor using Groq for oversight/safety layer.

This auditor provides a second-opinion review of buyer agent decisions,
flagging suspicious patterns that might technically pass policy checks
but warrant human review (e.g., unusual quantities, mismatched items,
oddly large orders, etc.).
"""
import os
import json
from typing import Optional, Dict, Any

# Configurable Groq model for auditing
# Can be overridden via GROQ_AUDITOR_MODEL environment variable
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


class AuditorError(Exception):
    """Raised when Groq API call fails."""
    pass


def _call_groq_with_model_fallback(client, model_name: str, messages: list, **kwargs):
    """Attempt completion with requested model, falling back to accessible models if not found."""
    try:
        resp = client.chat.completions.create(model=model_name, messages=messages, **kwargs)
        return resp, model_name
    except Exception as e:
        err_str = str(e)
        if "model_not_found" in err_str or "does not exist" in err_str or "404" in err_str:
            fallback_models = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
            for fm in fallback_models:
                if fm != model_name:
                    try:
                        resp = client.chat.completions.create(model=fm, messages=messages, **kwargs)
                        return resp, fm
                    except Exception:
                        continue
        raise


def audit_decision(
    goal: str,
    cart: list,
    catalog: dict,
    session_spend_so_far_paise: int = 0,
    session_history: Optional[list] = None,
    groq_api_key: str = "",
) -> Dict[str, Any]:
    """
    Run independent LLM audit review using Groq.
    
    Args:
        goal: Customer's stated shopping goal
        cart: Cart items [{'product': {...}, 'qty': ..., 'line_total_paise': ...}]
        catalog: Full catalog dict
        session_spend_so_far_paise: Previous session spend for context
        session_history: Previous orders in this session (if available)
        groq_api_key: Groq API key (passed from client to avoid server env var issues)
    
    Returns:
        Dict with:
        - risk_flag: "clean" or "flagged_for_review"
        - reasoning: Natural language explanation
        - model_used: Which Groq model was used
    """
    api_key = groq_api_key or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise AuditorError("GROQ_API_KEY not provided and not found in environment")
    
    # Get model name from environment variable or use default
    model_name = os.getenv("GROQ_AUDITOR_MODEL", DEFAULT_GROQ_MODEL)
    
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except ImportError:
        raise AuditorError("groq package not installed. Install with: pip install groq")
    
    # Build context for the auditor
    cart_summary = []
    total_paise = 0
    for item in cart:
        product = item["product"]
        qty = item["qty"]
        line_total = item["line_total_paise"]
        total_paise += line_total
        cart_summary.append(
            f"- {product['name']} (qty: {qty}, price: INR {product['price_paise']/100:.2f} each, "
            f"line total: INR {line_total/100:.2f})"
        )
    
    session_total_inr = (session_spend_so_far_paise + total_paise) / 100
    
    system_prompt = """You are an independent AI transaction auditor. Your job is to review purchases made by an AI buyer agent and flag potential issues for human review.

Analyze the shopping goal and proposed cart against these key business risks:
1. FAN FAVORITE DETECTION: If customer orders multiple units of high-demand items, check if total spend or quantity looks excessive.
2. FIRST-TIME BUYER SPIKE: If this is a new retail customer session with high spend (> INR 5,000), flag for verification. NOTE: For automated B2B warehouse inventory restock goals bringing stock back to healthy threshold, wholesale replenishment amounts within policy (up to INR 50,000) are normal operational procedure; do NOT flag them as first-time consumer buyer spikes.
3. GOAL-CART MISMATCH: Does the cart actually fulfill the stated goal? Flag nonsensical or irrelevant items.
4. QUANTITY ANOMALY: Are quantities reasonable for the goal? Flag bulk purchases without clear justification.

Respond with ONLY a JSON object:
{
  "risk_flag": "clean" | "flagged_for_review",
  "reasoning": "Clear, concise 1-2 sentence explanation of your assessment"
}"""

    user_message = f"""Shopping Goal: {goal}
Proposed Cart:
{chr(10).join(cart_summary)}

Session Context:
- Previous Spend: INR {session_spend_so_far_paise/100:.2f}
- Current Cart Total: INR {total_paise/100:.2f}
- Combined Session Total: INR {session_total_inr:.2f}
- Items in Session History: {len(session_history) if session_history else 0}
- Policy Caps: Single order INR {catalog.get('policy', {}).get('max_single_order_paise', 5000000)/100:.2f}, Session INR {catalog.get('policy', {}).get('max_session_spend_paise', 10000000)/100:.2f}

Review this decision and flag anything suspicious that might warrant human review, even if it passes policy thresholds."""

    try:
        # Use Groq's configurable model for quick auditing
        response, used_model = _call_groq_with_model_fallback(
            client,
            model_name=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,
            max_tokens=300,
        )
        
        audit_response = response.choices[0].message.content.strip()
        
        # Clean up response if it has markdown code blocks
        cleaned = audit_response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        
        result = json.loads(cleaned)
        
        # Validate structure
        if "risk_flag" not in result or "reasoning" not in result:
            raise AuditorError(f"Invalid auditor response structure: {result}")
        
        if result["risk_flag"] not in ["clean", "flagged_for_review"]:
            raise AuditorError(f"Invalid risk_flag value: {result['risk_flag']}")
        
        result["model_used"] = model_name
        return result
        
    except json.JSONDecodeError as e:
        raise AuditorError(f"Failed to parse auditor JSON response: {e}. Response was: {audit_response}")
    except Exception as e:
        raise AuditorError(f"Groq API call failed: {e}")


def _customer_order_fallback(tangible_items: list, reason_cause: str = "", company: str = "Afra Infra") -> Dict[str, Any]:
    """Fallback to random selection logic if Groq returns invalid data or fails."""
    import random
    picked = random.choice(tangible_items)
    
    # Random quantity (1-5, with bias towards threshold to trigger demo restocks)
    qty = random.randint(1, 5)
    threshold = picked.get("reorder_threshold", 0)
    if random.random() < 0.45 and picked["quantity"] > threshold:
        drop_qty = (picked["quantity"] - threshold) + random.randint(1, 3)
        if 0 < drop_qty <= picked["quantity"]:
            qty = drop_qty
    qty = min(qty, picked["quantity"])
    if qty <= 0:
        qty = 1
        
    comp_lower = company.lower() if company else "afra infra"
    if "tropicana" in comp_lower or "juice" in comp_lower:
        fallback_reasons = [
            f"Stocking up on {picked['name'].lower()} for our morning breakfast cafe",
            f"Running low on chilled juice cartons, ordering more {picked['name'].lower()}",
            f"Weekend brunch party preparations: need fresh {picked['name'].lower()}",
            f"Replenishing store display with popular {picked['name'].lower()}",
            f"Ordering customer favourite juice pack: {picked['name'].lower()}"
        ]
    elif "amul" in comp_lower or "dairy" in comp_lower:
        fallback_reasons = [
            f"Running low on essential dairy inventory, ordering fresh {picked['name'].lower()}",
            f"Weekly kitchen prep: need fresh batch of {picked['name'].lower()}",
            f"Restaurant breakfast service restock: ordering {picked['name'].lower()}",
            f"Pantry dairy essentials: stocking up on {picked['name'].lower()}",
            f"Baking and dessert prep: need authentic {picked['name'].lower()}"
        ]
    elif "minimalist" in comp_lower or "skin" in comp_lower:
        fallback_reasons = [
            f"Daily skincare routine replenishment: running low on {picked['name'].lower()}",
            f"Restocking holy-grail treatment product: {picked['name'].lower()}",
            f"Clinic supply order: client request for {picked['name'].lower()}",
            f"Refilling vanity skincare shelf with {picked['name'].lower()}",
            f"Reordering essential barrier-support formulation: {picked['name'].lower()}"
        ]
    elif "nestle" in comp_lower or "maggi" in comp_lower or "food" in comp_lower:
        fallback_reasons = [
            f"Pantry snack restock for study sessions: need {picked['name'].lower()}",
            f"Morning tea and coffee station restock: ordering {picked['name'].lower()}",
            f"Family weekend grocery replenishment: need {picked['name'].lower()}",
            f"Hostel quick-meal snack order: {picked['name'].lower()}",
            f"Kitchen dessert and treat preparation with {picked['name'].lower()}"
        ]
    else:
        fallback_reasons = [
            f"Running low on {picked['name'].lower()}, ordering more for ongoing site work",
            f"Emergency repairs required following roof inspection, need {picked['name'].lower()}",
            f"Contractor order: installing replacement roof section with {picked['name'].lower()}",
            f"Scheduled restock for upcoming commercial roofing project",
            f"Finishing shed roof extension before rainy weather, need {picked['name'].lower()}"
        ]
        
    reason = random.choice(fallback_reasons)
    
    return {
        "product": picked["name"],
        "sku": picked["sku"],
        "quantity": qty,
        "reason": reason,
        "generated_by": "fallback",
        "fallback_reason": reason_cause,
        "company": picked.get("company", company)
    }


def generate_customer_order(
    inventory_items: list,
    groq_api_key: str = "",
    company: str = "Afra Infra",
) -> Dict[str, Any]:
    """
    Generate a customer order using the same Groq client and model configuration as the auditor.
    Acts as a lightweight customer agent deciding which product to order, quantity, and a first-person reason.
    
    Returns:
        Dict with:
        - product: Product name
        - sku: Product SKU
        - quantity: int
        - reason: Short first-person reason string
        - generated_by: "groq" or "fallback"
    """
    tangible_items = [
        i for i in inventory_items
        if i.get("quantity", 0) > 0 and not str(i.get("sku", "")).startswith("sku_installation_")
    ]
    if not tangible_items:
        tangible_items = [i for i in inventory_items if i.get("quantity", 0) > 0]
    
    if not tangible_items:
        return {
            "product": "No stock available",
            "sku": "",
            "quantity": 0,
            "reason": f"All inventory items for {company} are currently out of stock",
            "generated_by": "fallback",
            "company": company
        }

    api_key = groq_api_key or os.getenv("GROQ_API_KEY")
    model_name = os.getenv("GROQ_AUDITOR_MODEL", DEFAULT_GROQ_MODEL)
    
    if not api_key:
        import logging
        logging.getLogger("customer_agent").info(
            "[Customer Agent Fallback] GROQ_API_KEY not provided or in env; using random-selection fallback."
        )
        return _customer_order_fallback(tangible_items, "GROQ_API_KEY not configured", company=company)

    try:
        from groq import Groq
        client = Groq(api_key=api_key, timeout=2.0)
    except Exception as e:
        import logging
        logging.getLogger("customer_agent").warning(
            f"[Customer Agent Fallback] Groq client init failed: {e}; using random-selection fallback."
        )
        return _customer_order_fallback(tangible_items, f"Groq init error: {e}", company=company)

    inventory_lines = [
        f"- {item['name']} (SKU: {item['sku']}, Stock: {item['quantity']})"
        for item in tangible_items
    ]
    
    system_prompt = (
        f"You are an active customer ordering products from {company}. "
        "Review the available inventory list and select ONE product you need, decide on a realistic quantity (between 1 and 5), "
        "and provide a short, realistic first-person reason for your order.\n\n"
        "You must respond with ONLY a single JSON object in this exact format, with NO markdown code blocks and NO other text:\n"
        "{\n"
        '  "product": "<product name from inventory>",\n'
        '  "quantity": <int>,\n'
        '  "reason": "<short first-person reason, e.g. \'Running low on supplies, ordering more\'>"\n'
        "}"
    )
    
    user_message = (
        f"Current {company} inventory list:\n" +
        "\n".join(inventory_lines) +
        "\n\nReturn the single JSON object for your order."
    )

    try:
        response, used_model = _call_groq_with_model_fallback(
            client,
            model_name=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            max_tokens=150,
        )
        
        raw_text = response.choices[0].message.content.strip()
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        
        data = json.loads(cleaned)
        
        # Validation
        product_query = str(data.get("product", "")).strip()
        qty = data.get("quantity")
        reason = str(data.get("reason", "")).strip()
        
        if not product_query or not isinstance(qty, (int, float)) or int(qty) <= 0 or not reason:
            raise ValueError(f"Malformed fields in response: {data}")
            
        qty = int(qty)
        
        # Validate that returned product exists in inventory
        matched = None
        p_lower = product_query.lower()
        for item in tangible_items:
            if p_lower == item["name"].lower() or p_lower == item["sku"].lower():
                matched = item
                break
        if not matched:
            for item in tangible_items:
                if p_lower in item["name"].lower() or item["name"].lower() in p_lower:
                    matched = item
                    break
        if not matched:
            raise ValueError(f"Product '{product_query}' does not match any current inventory item")
            
        # Ensure quantity does not exceed available stock
        qty = min(qty, matched["quantity"])
        if qty < 1:
            qty = 1
            
        return {
            "product": matched["name"],
            "sku": matched["sku"],
            "quantity": qty,
            "reason": reason,
            "generated_by": "groq",
            "model_used": used_model
        }
    except Exception as err:
        import logging
        logging.getLogger("customer_agent").warning(
            f"[Customer Agent Fallback] Groq order generation failed: {err}. Using random-selection fallback."
        )
        return _customer_order_fallback(tangible_items, str(err), company=company)