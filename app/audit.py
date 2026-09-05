"""
Append-only audit trail with multi-company support. Every money-relevant decision the agent makes --
whether it results in a Razorpay call or not -- gets one JSON line here.

Design goal: a human (or a regulator) should be able to reconstruct, from
this file alone, exactly what the agent considered, which policy checks it
ran, what it decided, and why -- without reading the code.
"""
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from . import config


@dataclass
class AuditEntry:
    entry_id: str
    timestamp: str
    session_id: str
    action_type: str                 # e.g. "cart_build", "policy_check", "upsell_offer",
                                      # "razorpay_order_create", "razorpay_failure", "checkout_result"
    summary: str                     # one-line human-readable explanation
    inputs: dict = field(default_factory=dict)
    policy_checks: list = field(default_factory=list)   # list of {rule, passed, detail}
    outcome: str = "info"            # "allowed" | "blocked" | "success" | "failure" | "info"
    razorpay: Optional[dict] = None  # raw (sanitised) gateway request/response, if any
    explanation: str = ""            # buyer-facing plain-English explanation
    company: str = "Afra Infra"      # company scope for the entry


class AuditTrail:
    def __init__(self, path: str = config.AUDIT_LOG_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def log(
        self,
        session_id: str,
        action_type: str,
        summary: str,
        inputs: Optional[dict] = None,
        policy_checks: Optional[list] = None,
        outcome: str = "info",
        razorpay: Optional[dict] = None,
        explanation: str = "",
        company: str = "Afra Infra",
    ) -> AuditEntry:
        # Resolve company if not provided or in inputs
        entry_company = company or (inputs or {}).get("company") or "Afra Infra"
        
        entry = AuditEntry(
            entry_id=str(uuid.uuid4())[:8],
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            session_id=session_id,
            action_type=action_type,
            summary=summary,
            inputs=inputs or {},
            policy_checks=policy_checks or [],
            outcome=outcome,
            razorpay=razorpay,
            explanation=explanation,
            company=entry_company,
        )
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")
        return entry

    def all_entries(self, company: Optional[str] = None) -> list:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    # Default legacy entries without company to Afra Infra
                    entry_company = data.get("company") or data.get("inputs", {}).get("company") or "Afra Infra"
                    data["company"] = entry_company
                    
                    if company:
                        c_lower = company.lower()
                        ec_lower = entry_company.lower()
                        if c_lower in ec_lower or ec_lower in c_lower or ("roof" in c_lower and "afra" in ec_lower):
                            out.append(data)
                    else:
                        out.append(data)
        return out

    def for_session(self, session_id: str, company: Optional[str] = None) -> list:
        entries = self.all_entries(company=company)
        return [e for e in entries if e["session_id"] == session_id]

    def clear(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    @staticmethod
    def render_markdown(entries: list) -> str:
        lines = ["| # | company | action | outcome | summary |", "|---|---|---|---|---|"]
        for i, e in enumerate(entries, 1):
            comp = e.get("company", "Afra Infra")
            lines.append(f"| {i} | **{comp}** | {e['action_type']} | **{e['outcome']}** | {e['summary']} |")
        return "\n".join(lines)
