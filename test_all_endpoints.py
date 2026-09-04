"""
Comprehensive test script for testing all endpoints used across all 4 tabs.
"""
import sys
import requests
import json

# Ensure stdout handles encoding safely on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8000"

def test_suite():
    print("========================================")
    print("Testing Backend Endpoints for all 4 Tabs")
    print("========================================")
    
    passed_count = 0
    failed_count = 0

    def run_step(step_num, title, fn):
        nonlocal passed_count, failed_count
        print(f"\n[{step_num}] {title}")
        try:
            msg = fn()
            print(f"[OK] {msg}")
            passed_count += 1
        except Exception as e:
            print(f"[FAIL] {title}: {e}")
            failed_count += 1

    # 1. Test Static Index Page
    def step1():
        r = requests.get(f"{BASE_URL}/")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        assert "Afra Infra" in r.text, "Missing 'Afra Infra'"
        assert "Customer Orders" in r.text, "Missing 'Customer Orders' tab"
        assert "AI Restock" in r.text, "Missing 'AI Restock' tab"
        assert "Inventory" in r.text, "Missing 'Inventory' tab"
        assert "AI Buyer Checkout" in r.text, "Missing 'AI Buyer Checkout' tab"
        return "Static page served correctly with all 4 tabs."
    run_step(1, "GET / (Static UI)", step1)

    # 2. Test Catalog Manifest (.well-known/agent-catalog.json)
    def step2():
        r = requests.get(f"{BASE_URL}/.well-known/agent-catalog.json")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        catalog = r.json()
        assert "products" in catalog, "Catalog missing 'products'"
        assert len(catalog["products"]) > 0, "No products in catalog"
        return f"Catalog manifest loaded: {len(catalog['products'])} products found."
    run_step(2, "GET /.well-known/agent-catalog.json", step2)

    # 3. Test Inventory List
    def step3():
        r = requests.get(f"{BASE_URL}/inventory")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        inv = r.json()
        assert len(inv) > 0, "Inventory is empty"
        return f"Inventory fetched: {len(inv)} items tracked."
    run_step(3, "GET /inventory", step3)

    # 4. Test Customer Order (Depletion)
    def step4():
        order_req = {
            "items": [
                {"sku": "sku_roof_sheet_std", "qty": 5},
                {"sku": "sku_ridge_cap", "qty": 2}
            ],
            "notes": "Integration test customer order",
            "auto_restock": False
        }
        r = requests.post(f"{BASE_URL}/customer-order", json=order_req)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        order_res = r.json()
        assert order_res.get("success") is True, f"Order failed: {order_res}"
        return f"Customer order placed: {order_res['message']}"
    run_step(4, "POST /customer-order", step4)

    # 5. Test Customer Orders List
    def step5():
        r = requests.get(f"{BASE_URL}/customer-orders?limit=5")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        cust_orders = r.json()
        assert len(cust_orders) > 0, "No customer orders retrieved"
        return f"Customer orders history retrieved: {len(cust_orders)} orders."
    run_step(5, "GET /customer-orders", step5)

    # 6. Test Inventory Threshold Check
    def step6():
        r = requests.get(f"{BASE_URL}/inventory/check")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        low_stock = r.json()
        return f"Low stock items found: {len(low_stock)}"
    run_step(6, "GET /inventory/check", step6)

    # 7. Test AI Restock Check & Trigger
    def step7():
        restock_req = {
            "gemini_api_key": "",
            "groq_api_key": ""
        }
        r = requests.post(f"{BASE_URL}/restock/check", json=restock_req)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        restock_res = r.json()
        return f"Restock check completed: {restock_res.get('message', 'done')}"
    run_step(7, "POST /restock/check", step7)

    # 8. Test Restock Orders History & Approval
    def step8():
        r = requests.get(f"{BASE_URL}/restock-orders?limit=5")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        restock_orders = r.json()
        return f"Restock orders history retrieved: {len(restock_orders)} orders."
    run_step(8, "GET /restock-orders", step8)

    # 9. Test AI Buyer Checkout Flow
    def step9():
        checkout_req = {
            "session_id": "test_buyer_session_001",
            "items": [
                {"product_id": "sku_clay_tile", "qty": 2}
            ],
            "accept_upsell": True,
            "buyer_confirmed_high_value": False,
            "with_auditor": False,
            "customer_goal": "Need clay tiles for garden canopy",
            "groq_api_key": "",
            "gemini_api_key": ""
        }
        r = requests.post(f"{BASE_URL}/agent/checkout", json=checkout_req)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        checkout_res = r.json()
        assert checkout_res.get("success") is True, f"Checkout failed: {checkout_res}"
        return f"AI Buyer checkout completed: {checkout_res['message']} (Total: INR {checkout_res['order_total_paise']/100:.2f})"
    run_step(9, "POST /agent/checkout", step9)

    # 10. Test Audit Trail
    def step10():
        r = requests.get(f"{BASE_URL}/audit?session_id=test_buyer_session_001")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        audit_entries = r.json()
        assert len(audit_entries) > 0, "No audit entries returned"
        return f"Audit trail retrieved: {len(audit_entries)} log entries for session."
    run_step(10, "GET /audit", step10)

    print("\n========================================")
    if failed_count == 0:
        print(f"ALL {passed_count} ENDPOINT / INTEGRATION TESTS PASSED! [OK]")
    else:
        print(f"TESTS FINISHED: {passed_count} PASSED, {failed_count} FAILED.")
    print("========================================")
    return failed_count == 0

if __name__ == "__main__":
    success = test_suite()
    sys.exit(0 if success else 1)
