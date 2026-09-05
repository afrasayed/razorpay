import requests
import json
import sqlite3
import time
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:8000"

def run_test():
    print("==================================================")
    print("STEP 1: Resetting inventory for Afra Infra")
    print("==================================================")
    r = requests.post(f"{BASE_URL}/inventory/reset")
    print("Reset response:", r.json())
    assert r.status_code == 200

    # Get baseline inventory
    r = requests.get(f"{BASE_URL}/inventory?company=Afra%20Infra")
    inv = {item["sku"]: item for item in r.json()}
    print(f"Baseline: sku_roof_sheet_std qty={inv['sku_roof_sheet_std']['quantity']}, threshold={inv['sku_roof_sheet_std']['reorder_threshold']}")
    print(f"Baseline: sku_roof_sheet_premium qty={inv['sku_roof_sheet_premium']['quantity']}, threshold={inv['sku_roof_sheet_premium']['reorder_threshold']}")

    # Get count of restock orders before test
    conn = sqlite3.connect("data/inventory.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM restock_orders")
    initial_restock_count = c.fetchone()[0]
    conn.close()

    print("\n==================================================")
    print("STEP 2: Depleting SKU 1 (sku_roof_sheet_std)")
    print("==================================================")
    # Order 40 units (stock goes from 50 -> 10, threshold is 15)
    deplete_order_1 = {
        "items": [{"sku": "sku_roof_sheet_std", "qty": 40}],
        "notes": "Testing auto-restock single trigger for standard roof sheets",
        "company": "Afra Infra",
        "auto_restock": True
    }
    r = requests.post(f"{BASE_URL}/customer-order", json=deplete_order_1)
    res1 = r.json()
    print("Customer Order 1 response:", json.dumps(res1, indent=2))
    assert res1.get("success") == True

    auto1 = res1.get("auto_restock", {})
    assert auto1.get("triggered") == True
    decisions1 = auto1.get("restock_decisions", [])
    print(f"Decisions triggered: {len(decisions1)}")
    assert len(decisions1) == 1
    d1 = decisions1[0]
    print(f"SKU: {d1['sku']}, Restock Qty: {d1['restock_qty']}, Total: INR {d1['total_paise']/100}")
    print("Checkout result:", d1["checkout_result"])
    assert d1["checkout_result"]["success"] == True
    print("--> PASS: Restock Order 1 SUCCESSFUL & Stock Updated!")

    print("\n==================================================")
    print("STEP 3: Depleting SKU 2 (sku_roof_sheet_premium)")
    print("==================================================")
    # Order 25 units (stock goes from 30 -> 5, threshold is 10)
    deplete_order_2 = {
        "items": [{"sku": "sku_roof_sheet_premium", "qty": 25}],
        "notes": "Testing auto-restock single trigger for premium roof sheets",
        "company": "Afra Infra",
        "auto_restock": True
    }
    r = requests.post(f"{BASE_URL}/customer-order", json=deplete_order_2)
    res2 = r.json()
    print("Customer Order 2 response:", json.dumps(res2, indent=2))
    assert res2.get("success") == True

    auto2 = res2.get("auto_restock", {})
    assert auto2.get("triggered") == True
    decisions2 = auto2.get("restock_decisions", [])
    print(f"Decisions triggered: {len(decisions2)}")
    assert len(decisions2) == 1
    d2 = decisions2[0]
    print(f"SKU: {d2['sku']}, Restock Qty: {d2['restock_qty']}, Total: INR {d2['total_paise']/100}")
    print("Checkout result:", d2["checkout_result"])
    assert d2["checkout_result"]["success"] == True
    print("--> PASS: Restock Order 2 SUCCESSFUL & Stock Updated!")

    print("\n==================================================")
    print("STEP 4: Verifying Inventory Levels After Restock")
    print("==================================================")
    r = requests.get(f"{BASE_URL}/inventory?company=Afra%20Infra")
    new_inv = {item["sku"]: item for item in r.json()}
    # sku_roof_sheet_std: was 50 - 40 = 10; restocked 20 -> should be 30
    print(f"sku_roof_sheet_std quantity: {new_inv['sku_roof_sheet_std']['quantity']} (expected 30)")
    assert new_inv['sku_roof_sheet_std']['quantity'] == 30

    # sku_roof_sheet_premium: was 30 - 25 = 5; restocked 15 -> should be 20
    print(f"sku_roof_sheet_premium quantity: {new_inv['sku_roof_sheet_premium']['quantity']} (expected 20)")
    assert new_inv['sku_roof_sheet_premium']['quantity'] == 20

    print("\n==================================================")
    print("STEP 5: Verifying Deduplication (No duplicate restocks)")
    print("==================================================")
    # Check SQLite DB: exactly 2 new restock orders should have been created
    conn = sqlite3.connect("data/inventory.db")
    c = conn.cursor()
    c.execute("SELECT id, order_date, status, company, items, total_paise FROM restock_orders ORDER BY id DESC LIMIT 5")
    recent_orders = c.fetchall()
    conn.close()

    print("Most recent restock orders in DB:")
    for o in recent_orders:
        print(f"  Order #{o[0]} - Date: {o[1]} - Status: {o[2]} - Comp: {o[3]} - Total: INR {o[5]/100:.2f} - Items: {o[4]}")

    # Top two orders should be the ones from Step 2 and Step 3
    assert recent_orders[0][2] == "completed"
    assert recent_orders[1][2] == "completed"

    # Now test calling /restock/check when stock is healthy
    r = requests.post(f"{BASE_URL}/restock/check", json={"company": "Afra Infra"})
    check_res = r.json()
    print("Restock check result:", json.dumps(check_res, indent=2))
    assert len(check_res.get("restock_decisions", [])) == 0
    assert "No restock needed" in check_res.get("message", "")
    print("--> PASS: No duplicate restocks triggered!")

    print("\n==================================================")
    print("STEP 6: High-Value SKU Handling (sku_industrial_bulk_order)")
    print("==================================================")
    # Deplete sku_industrial_bulk_order (stock 2 -> 0, threshold 1)
    deplete_bulk = {
        "items": [{"sku": "sku_industrial_bulk_order", "qty": 2}],
        "notes": "Testing high value capital item restock behavior",
        "company": "Afra Infra",
        "auto_restock": True
    }
    r = requests.post(f"{BASE_URL}/customer-order", json=deplete_bulk)
    res_bulk = r.json()
    print("Bulk Order Customer response:", json.dumps(res_bulk, indent=2))
    bulk_decisions = res_bulk.get("auto_restock", {}).get("restock_decisions", [])
    assert len(bulk_decisions) == 1
    bd = bulk_decisions[0]
    print(f"Bulk SKU: {bd['sku']}, Qty: {bd['restock_qty']}, Total: INR {bd['total_paise']/100}")
    print("Checkout result:", bd["checkout_result"])
    assert bd["restock_qty"] == 1  # Exactly 1 unit!
    assert bd["total_paise"] == 15500000  # INR 1,55,000
    assert bd["checkout_result"]["needs_approval"] == True  # Held for human approval by design!

    # Test approving the held order
    pending_order_id = bd["order_id"]
    print(f"Testing human approval for Restock Order #{pending_order_id} via /restock/approve...")
    r_app = requests.post(f"{BASE_URL}/restock/approve", json={"order_id": pending_order_id, "action": "approve"})
    print("Approve response:", r_app.json())
    assert r_app.json().get("success") == True

    # Verify inventory is updated to 1
    r = requests.get(f"{BASE_URL}/inventory?company=Afra%20Infra")
    final_inv = {item["sku"]: item for item in r.json()}
    print(f"sku_industrial_bulk_order quantity after approval: {final_inv['sku_industrial_bulk_order']['quantity']} (expected 1)")
    assert final_inv['sku_industrial_bulk_order']['quantity'] == 1
    print("--> PASS: High-value SKU properly held and successfully approved!")

    print("\n==================================================")
    print("ALL TESTS PASSED WITH 100% SUCCESS!")
    print("==================================================")

if __name__ == "__main__":
    run_test()
