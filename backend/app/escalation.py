"""
escalation.py
Deterministic rules engine that backstops the LLM's own judgment about when
to hand off to a human. The agent is instructed (via system prompt + the
escalation_policy.md doc) to call escalate_to_human on its own, but for the
handful of hard financial/fraud/safety rules we ALSO enforce them in code so
a prompt-following mistake can't cause an unapproved high-risk action.
"""

from datetime import datetime

REFUND_AUTO_APPROVE_LIMIT = 5000  # INR


def refund_requires_human_approval(amount: float, has_return: bool, reason: str,
                                    recent_refund_count: int) -> tuple[bool, str]:
    """Returns (requires_approval, explanation)."""
    if amount >= REFUND_AUTO_APPROVE_LIMIT:
        return True, f"Refund amount ₹{amount:.2f} meets/exceeds the ₹{REFUND_AUTO_APPROVE_LIMIT} auto-approval limit."
    if not has_return and reason not in ("missing_item", "delivery_delay_credit"):
        return True, "Refund requested without an associated return."
    if recent_refund_count > 2:
        return True, f"Customer has {recent_refund_count} refund requests in the last 30 days (possible abuse pattern)."
    return False, "Within auto-approval limits."


def order_possibly_lost(last_tracking_update: str, status: str) -> bool:
    if status == "delivered":
        return False
    try:
        last = datetime.fromisoformat(last_tracking_update)
    except (ValueError, TypeError):
        return False
    return (datetime.now() - last).days >= 5
