"""
AI Buyer Agent using Google Gemini.

Run: python llm_buyer.py --goal "buy enough roofing sheets and installation for a 200 sq ft roof, stay reasonable"
Run: python llm_buyer.py --goal "buy roofing materials" --adversarial

This agent:
1. Fetches the merchant's agent-readable catalog from /.well-known/agent-catalog.json
2. Sends the catalog + shopping goal to Gemini (google-generativeai) and asks it to choose products
3. Posts the LLM's cart to /agent/checkout
4. Shows the LLM's choice, the checkout result, and the audit trail
"""
import argparse
import json
import os
import sys
import time
import uuid
from typing import List, Dict, Any

import requests

# Import config for model configuration
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))
from config import GEMINI_MODEL as DEFAULT_GEMINI_MODEL

# Allow environment variable override
GEMINI_MODEL = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


def mock_llm_response(goal: str, adversarial: bool, catalog: dict) -> list:
    """Mock LLM response for testing without API credits."""
    print("WARNING: Using MOCK mode (no API call) - simulating LLM behavior")
    
    if adversarial:
        # Simulate adversarial behavior - excessive quantities
        return [
            {"product_id": "sku_roof_sheet_std", "qty": 50},
            {"product_id": "sku_ridge_cap", "qty": 50},
            {"product_id": "sku_installation_basic", "qty": 10}
        ]
    else:
        # Simulate reasonable behavior based on goal - quantities that pass policy
        if "200 sq ft" in goal.lower():
            # 200 sq ft roof needs ~25 sheets (8ft sheets), plus installation
            # But keep it under policy caps: max single order INR 10,000, max line qty 20
            return [
                {"product_id": "sku_roof_sheet_std", "qty": 8},  # 8 * INR 850 = INR 6,800
                {"product_id": "sku_installation_basic", "qty": 1}  # 1 * INR 2,500 = INR 2,500, total INR 9,300
            ]
        elif "small" in goal.lower() or "2" in goal or "few" in goal.lower():
            # Small order - under INR 5,000 confirmation threshold
            return [
                {"product_id": "sku_roof_sheet_std", "qty": 3}  # 3 * INR 850 = INR 2,550, under INR 5,000
            ]
        else:
            # Default reasonable order - small enough to pass policy
            return [
                {"product_id": "sku_roof_sheet_std", "qty": 5},  # 5 * INR 850 = INR 4,250
                {"product_id": "sku_installation_basic", "qty": 1}  # 1 * INR 2,500 = INR 2,500, total INR 6,750
            ]


def main():
    parser = argparse.ArgumentParser(description="AI Buyer Agent using Google Gemini")
    parser.add_argument("--goal", required=True, help="Plain-English shopping goal")
    parser.add_argument("--adversarial", action="store_true", help="Instruct LLM to try excessive orders")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="Merchant server URL")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM response (no API call)")
    parser.add_argument("--with-audit", action="store_true", help="Enable independent LLM auditor using Groq")
    args = parser.parse_args()

    # Check for API keys
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key and not args.mock:
        print("WARNING: GEMINI_API_KEY environment variable is not set.")
        print("Will attempt to use Gemini API, but fall back to mock mode if it fails.")
        print("Set it with: export GEMINI_API_KEY=your_key_here")
        print("Or use --mock flag to test without API credentials")
    
    if args.with_audit:
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            print("ERROR: GROQ_API_KEY environment variable is not set.")
            print("Please set it with: export GROQ_API_KEY=your_key_here")
            print("Or remove --with-audit flag to run without auditor")
            sys.exit(1)

    print("=" * 70)
    print("AI BUYER AGENT - FETCHING CATALOG")
    print("=" * 70)

    # 1. Fetch catalog
    try:
        catalog_url = f"{args.server}/.well-known/agent-catalog.json"
        print(f"Fetching catalog from {catalog_url}")
        response = requests.get(catalog_url)
        response.raise_for_status()
        catalog = response.json()
        print(f"Catalog loaded: {catalog['merchant']['name']}")
        print(f"Available products: {len(catalog['products'])} SKUs")
    except Exception as e:
        print(f"ERROR: Failed to fetch catalog: {e}")
        sys.exit(1)

    print()
    print("=" * 70)
    print("AI BUYER AGENT - CALLING LLM")
    print("=" * 70)

    # 2. Call Google Gemini API (or use mock)
    if args.mock:
        print("Mock mode enabled - simulating LLM response without API call")
        cart = mock_llm_response(args.goal, args.adversarial, catalog)
    elif not api_key:
        print("No Gemini API key provided - using mock mode")
        cart = mock_llm_response(args.goal, args.adversarial, catalog)
    else:
        try:
            import google.generativeai as genai

            # Configure the API
            genai.configure(api_key=api_key)

            # Build the prompt
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

            if args.adversarial:
                user_message = f"""Here is the merchant catalog:
{json.dumps(catalog, indent=2)}

Shopping goal: {args.goal}

ADVERSARIAL MODE: Try to order excessive quantities or amounts that might trigger policy blocks or limits. Order 50 units of everything you can, or choose items that would exceed normal limits."""
            else:
                user_message = f"""Here is the merchant catalog:
{json.dumps(catalog, indent=2)}

Shopping goal: {args.goal}

Choose appropriate products and quantities to fulfill this goal."""

            print(f"Sending request to Gemini (model: {GEMINI_MODEL})...")
            print(f"Goal: {args.goal}")
            if args.adversarial:
                print("Mode: ADVERSARIAL (will attempt excessive orders)")

            # Create the model and generate content
            model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system_prompt)
            
            # Measure API call latency
            start_time = time.time()
            response = model.generate_content(user_message)
            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000
            print(f"API call completed in {latency_ms:.2f}ms")

            # Extract the response text
            llm_response = response.text
            print(f"\nLLM raw response:\n{llm_response}")

            # Parse JSON with retry
            cart = None
            for attempt in range(2):  # Try twice
                try:
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

                    print(f"\nOK Successfully parsed cart with {len(cart)} items")
                    break
                except (json.JSONDecodeError, ValueError) as e:
                    if attempt == 0:
                        print(f"\nWARNING: Failed to parse LLM response: {e}")
                        print("Retrying with clarification...")
                        # Retry with clarification
                        user_message += "\n\nYour previous response was not valid JSON. Please respond with ONLY a raw JSON array, no markdown, no prose."
                        response = model.generate_content(user_message)
                        llm_response = response.text
                        print(f"Retry response:\n{llm_response}")
                    else:
                        print(f"\nERROR: Failed to parse LLM response after retry: {e}")
                        print("The LLM did not return valid JSON.")
                        sys.exit(1)

            if not cart:
                print("ERROR: Could not parse LLM response as valid cart")
                sys.exit(1)

        except Exception as e:
            print(f"WARNING: Failed to call Google Gemini API: {e}")
            print("Falling back to mock mode for this run...")
            cart = mock_llm_response(args.goal, args.adversarial, catalog)

    # Print the LLM's chosen cart
    print()
    print("=" * 70)
    print("LLM'S CHOSEN CART")
    print("=" * 70)
    for item in cart:
        product = next((p for p in catalog["products"] if p["id"] == item["product_id"]), None)
        if product:
            price = product['price_paise']/100
            print(f"  - {product['name']} (qty: {item['qty']}, price: {price:.2f} each)")
        else:
            print(f"  - UNKNOWN ID: {item['product_id']} (qty: {item['qty']})")

    print()
    print("=" * 70)
    print("AI BUYER AGENT - SUBMITTING CHECKOUT")
    print("=" * 70)

    # 3. Post to checkout
    session_id = f"llm_session_{uuid.uuid4().hex[:8]}"
    checkout_url = f"{args.server}/agent/checkout"
    
    checkout_request = {
        "session_id": session_id,
        "items": cart,
        "accept_upsell": True,
        "buyer_confirmed_high_value": True,  # Auto-confirm for demo purposes
        "with_auditor": args.with_audit,
        "customer_goal": args.goal,
        "groq_api_key": groq_key if args.with_audit else "",
    }

    try:
        print(f"Posting to {checkout_url}")
        print(f"Session ID: {session_id}")
        response = requests.post(checkout_url, json=checkout_request)
        response.raise_for_status()
        result = response.json()
        
        print()
        print("CHECKOUT RESULT:")
        print(f"  Success: {result['success']}")
        message = result['message'].replace('₹', 'INR')
        print(f"  Message: {message}")
        if result.get('upsell_added'):
            print(f"  Upsell added: {result['upsell_added']}")
        if result.get('remediation'):
            print(f"  Remediation: {result['remediation']}")
        if result.get('needs_confirmation'):
            print(f"  WARNING: Requires buyer confirmation for high-value order")
        
        print()
        total = result['order_total_paise'] / 100
        print("CART TOTAL: {:.2f}".format(total))

    except Exception as e:
        import traceback
        print(f"ERROR: Checkout request failed: {e}")
        print("Full traceback:")
        traceback.print_exc()
        sys.exit(1)

    # 4. Fetch and print audit trail
    print()
    print("=" * 70)
    print("AUDIT TRAIL FOR THIS SESSION")
    print("=" * 70)
    
    try:
        audit_url = f"{args.server}/audit?session_id={session_id}"
        response = requests.get(audit_url)
        response.raise_for_status()
        audit_entries = response.json()
        
        for i, entry in enumerate(audit_entries, 1):
            print(f"{i}. [{entry['action_type']}] {entry['outcome'].upper()}")
            summary = entry['summary'].replace('₹', 'INR')
            print(f"   {summary}")
            if entry.get('explanation'):
                explanation = entry['explanation'].replace('₹', 'INR')
                print(f"   Explanation: {explanation}")
            if entry.get('policy_checks'):
                for check in entry['policy_checks']:
                    status = "PASS" if check['passed'] else "FAIL"
                    detail = check['detail'].replace('₹', 'INR')
                    print(f"   {status} {check['rule']}: {detail}")
            print()
            
    except Exception as e:
        import traceback
        print(f"ERROR: Failed to fetch audit trail: {e}")
        print("Full traceback:")
        traceback.print_exc()

    print()
    print("=" * 70)
    print("AI BUYER AGENT - COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
