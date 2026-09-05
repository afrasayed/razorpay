import requests
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:8000"

def test_auditor_scenarios():
    print("==================================================")
    print("TEST 1: Genuine Auditor Success (CLEAN / APPROVED)")
    print("==================================================")
    # A normal small order matching goal
    clean_payload = {
        "session_id": "test_auditor_clean_session",
        "items": [{"product_id": "sku_roof_sheet_std", "qty": 2}],
        "customer_goal": "Need 2 standard roofing sheets for emergency shed leak repair",
        "with_auditor": True,
        "company": "Afra Infra"
    }
    r = requests.post(f"{BASE_URL}/agent/checkout", json=clean_payload)
    data = r.json()
    print("Success status:", data.get("success"))
    audit_res = data.get("audit_result")
    print("Audit result:", json.dumps(audit_res, indent=2))
    assert audit_res is not None, "audit_result should be present"
    assert audit_res.get("risk_flag") == "clean", f"Expected 'clean', got {audit_res.get('risk_flag')}"
    print("--> PASS: Genuine clean approval returned from Groq auditor!")

    print("\n==================================================")
    print("TEST 2: Genuine Auditor Flag (FLAGGED FOR REVIEW)")
    print("==================================================")
    # A suspicious mismatch order: goal says repair small leak, but orders 35 sheets near line cap
    flag_payload = {
        "session_id": "test_auditor_flag_session",
        "items": [
            {"product_id": "sku_roof_sheet_std", "qty": 35},
            {"product_id": "sku_ridge_cap", "qty": 10}
        ],
        "customer_goal": "Need a couple screws to fix a tiny 1-foot patch of ceiling",
        "with_auditor": True,
        "company": "Afra Infra"
    }
    r = requests.post(f"{BASE_URL}/agent/checkout", json=flag_payload)
    data = r.json()
    print("Checkout success (should be False due to hold):", data.get("success"))
    print("Needs approval:", data.get("needs_approval"))
    audit_res = data.get("audit_result")
    print("Audit result:", json.dumps(audit_res, indent=2))
    assert audit_res is not None, "audit_result should be present"
    assert audit_res.get("risk_flag") == "flagged_for_review", f"Expected 'flagged_for_review', got {audit_res.get('risk_flag')}"
    print("--> PASS: Suspicious order successfully flagged by Groq auditor!")

    print("\n==================================================")
    print("TEST 3: Auditor Call Failure (INVALID KEY / UNAVAILABLE)")
    print("==================================================")
    # Provide an intentionally broken Groq key
    fail_payload = {
        "session_id": "test_auditor_fail_session",
        "items": [{"product_id": "sku_roof_sheet_std", "qty": 1}],
        "customer_goal": "Need 1 roofing sheet",
        "with_auditor": True,
        "groq_api_key": "invalid_test_key_mock",
        "company": "Afra Infra"
    }
    r = requests.post(f"{BASE_URL}/agent/checkout", json=fail_payload)
    data = r.json()
    print("Checkout success (proceeds per fail-open policy):", data.get("success"))
    audit_res = data.get("audit_result")
    print("Audit result:", json.dumps(audit_res, indent=2))
    assert audit_res is not None, "audit_result should be present"
    assert audit_res.get("risk_flag") == "error", f"Expected risk_flag 'error', got {audit_res.get('risk_flag')}"
    assert "unavailable" in audit_res.get("reasoning", "").lower() or "failed" in audit_res.get("reasoning", "").lower()
    print("--> PASS: Failed auditor call returns explicit error status and NEVER 'approved'!")

    print("\n==================================================")
    print("TEST 4: Verifying Audit Log API for Latest Review")
    print("==================================================")
    r = requests.get(f"{BASE_URL}/audit?company=Afra%20Infra")
    entries = r.json()
    # Find most recent llm_audit_review in reverse
    latest_review = next((e for e in reversed(entries) if e.get("action_type") == "llm_audit_review"), None)
    assert latest_review is not None
    print(f"Latest review in audit log: outcome='{latest_review.get('outcome')}', summary='{latest_review.get('summary')}'")
    assert latest_review.get("outcome") == "failure"
    assert "invalid_test_key" in latest_review.get("summary", "") or "401" in latest_review.get("summary", "")
    print("--> PASS: Audit log correctly recorded failure for the broken key session!")

    print("\n==================================================")
    print("ALL AUDITOR SCENARIO TESTS COMPLETED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    test_auditor_scenarios()
