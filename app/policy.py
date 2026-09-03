"""
The gate. Nothing in agent.py is allowed to call the Razorpay client until
every rule here has been evaluated and logged. Each rule returns a
PolicyCheck with a human-readable `detail` so the audit trail never contains
an opaque true/false with no explanation.
"""
from dataclasses import dataclass
from typing import List

from . import config


@dataclass
class PolicyCheck:
    rule: str
    passed: bool
    detail: str

    def as_dict(self):
        return {"rule": self.rule, "passed": self.passed, "detail": self.detail}


def evaluate_cart(cart: list, session_spend_so_far_paise: int, categories_seen: set) -> List[PolicyCheck]:
    """cart: list of {product, qty, line_total_paise}"""
    checks: List[PolicyCheck] = []

    order_total = sum(item["line_total_paise"] for item in cart)

    checks.append(PolicyCheck(
        rule="max_single_order",
        passed=order_total <= config.MAX_SINGLE_ORDER_PAISE,
        detail=f"Order total INR {order_total/100:.2f} vs cap INR {config.MAX_SINGLE_ORDER_PAISE/100:.2f}",
    ))

    projected_session = session_spend_so_far_paise + order_total
    checks.append(PolicyCheck(
        rule="max_session_spend",
        passed=projected_session <= config.MAX_SESSION_SPEND_PAISE,
        detail=(f"Session total after this order would be INR {projected_session/100:.2f} "
                f"vs cap INR {config.MAX_SESSION_SPEND_PAISE/100:.2f}"),
    ))

    for item in cart:
        ok = item["qty"] <= config.MAX_LINE_QTY
        checks.append(PolicyCheck(
            rule="max_line_qty",
            passed=ok,
            detail=f"{item['product']['id']} qty={item['qty']} vs cap {config.MAX_LINE_QTY}",
        ))
        cat_ok = item["product"]["category"] in config.ALLOWED_CATEGORIES
        checks.append(PolicyCheck(
            rule="allowed_category",
            passed=cat_ok,
            detail=f"{item['product']['id']} category={item['product']['category']}",
        ))

    requires_confirmation = order_total >= config.HUMAN_CONFIRM_ABOVE_PAISE
    checks.append(PolicyCheck(
        rule="human_confirmation_threshold",
        passed=True,  # informational, not a blocker -- it changes *how* we proceed, not *whether*
        detail=(f"INR {order_total/100:.2f} {'>=' if requires_confirmation else '<'} "
                f"INR {config.HUMAN_CONFIRM_ABOVE_PAISE/100:.2f} confirmation threshold "
                f"-> {'buyer confirmation required before charge' if requires_confirmation else 'auto-proceed allowed'}"),
    ))

    return checks


def requires_human_confirmation(cart: list) -> bool:
    order_total = sum(item["line_total_paise"] for item in cart)
    return order_total >= config.HUMAN_CONFIRM_ABOVE_PAISE


def all_passed(checks: List[PolicyCheck]) -> bool:
    return all(c.passed for c in checks if c.rule != "human_confirmation_threshold")
