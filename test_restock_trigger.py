"""
Test AI restock trigger logic.
"""
import requests
import json

# First, deplete inventory to get below threshold
print("Step 1: Depleting inventory to trigger restock...")
deplete_order = {
    "items": [
        {"sku": "sku_roof_sheet_std", "qty": 40}  # This will bring it from 50 to 10, which is below threshold of 15
    ],
    "notes": "Test order to deplete stock"
}

response = requests.post("http://127.0.0.1:8000/customer-order", json=deplete_order)
print(f"Deplete result: {response.json()}")

# Check current inventory
print("\nStep 2: Checking current inventory...")
inventory_response = requests.get("http://127.0.0.1:8000/inventory")
inventory = inventory_response.json()
for item in inventory:
    print(f"  {item['sku']}: {item['quantity']} (threshold: {item['reorder_threshold']})")

# Check for items below threshold
print("\nStep 3: Checking for items below threshold...")
threshold_response = requests.get("http://127.0.0.1:8000/inventory/check")
low_stock = threshold_response.json()
print(f"Low stock items: {len(low_stock)}")
for item in low_stock:
    print(f"  {item['sku']}: {item['quantity']} (threshold: {item['reorder_threshold']})")

# Trigger AI restock check
print("\nStep 4: Triggering AI restock check...")
restock_request = {
    "gemini_api_key": "AQ.Ab8RN6L_SCh8wRMxI5VmVKBx0ggYr5yHHHxKcDSVAmicCPMsWA",
    "groq_api_key": ""
}

restock_response = requests.post("http://127.0.0.1:8000/restock/check", json=restock_request)
restock_result = restock_response.json()
print(f"Restock check result: {json.dumps(restock_result, indent=2)}")

print("\nTest completed!")