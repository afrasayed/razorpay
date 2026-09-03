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
    
    # Count previous orders if history provided
    prev_orders_count = len(session_history) if session_history else 0
    
    system_prompt = """You are an independent AI auditor reviewing e-commerce checkout decisions for potential fraud or unusual patterns.

Your task is to review the buyer agent's cart decision and flag anything suspicious, even if it technically passes standard policy checks.

Consider red flags like:
- Unusually large quantities for a first-time customer
- Items that don't match the stated shopping goal
- Orders that are oddly large relative to typical size
- Rapid successive orders that might indicate testing/exploitation
- Any pattern that suggests automated abuse rather than genuine shopping

You must respond with ONLY a JSON object with this exact structure:
{
  "risk_flag": "clean" or "flagged_for_review",
  "reasoning": "brief natural language explanation of your assessment"
}

Do NOT include any prose, explanations, or markdown formatting. Just the raw JSON object."""

    user_message = f"""Shopping Goal: {goal}

Cart Decision (chosen by buyer agent):
{chr(10).join(cart_summary)}

Cart Total: INR {total_paise/100:.2f}
Session Total After This Order: INR {session_total_inr:.2f}
Previous Orders in This Session: {prev_orders_count}

Catalog Context:
- Merchant: {catalog['merchant']['name']}
- Available Products: {len(catalog['products'])} SKUs
- Policy Caps: Single order INR {catalog.get('policy', {}).get('max_single_order_paise', 1000000)/100:.2f}, Session INR {catalog.get('policy', {}).get('max_session_spend_paise', 2000000)/100:.2f}

Review this decision and flag anything suspicious that might warrant human review, even if it passes policy thresholds."""

    try:
        # Use Groq's configurable model for quick auditing
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,  # Lower temperature for more consistent risk assessment
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