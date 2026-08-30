"""
tools.py
The concrete actions ORD Bot can take, exposed to the LLM as Anthropic
tool-use functions. Each function reads/writes the SQLite mock store and
returns a plain dict (JSON-serializable) that gets fed back to the model
as a tool_result.

Every tool is intentionally narrow and auditable: the agent cannot do
anything the tool functions don't explicitly allow, and every state-changing
tool logs what it did.
"""

import json
import uuid
from datetime import datetime, timedelta

from database import get_connection, row_to_dict
from policy_search import search_policies
from escalation import refund_requires_human_approval, order_possibly_lost, REFUND_AUTO_APPROVE_LIMIT


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def get_customer_orders(customer_id: str):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date DESC", (customer_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return {"found": False, "message": f"No orders found for customer_id {customer_id}."}
    orders = []
    for r in rows:
        d = row_to_dict(r)
        d["items"] = json.loads(d.pop("items_json"))
        orders.append(d)
    return {"found": True, "orders": orders}


def track_order(order_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    conn.close()
    if not row:
        return {"found": False, "message": f"No order found with id {order_id}."}
    d = row_to_dict(row)
    d["items"] = json.loads(d.pop("items_json"))
    possibly_lost = order_possibly_lost(d.get("last_tracking_update"), d["status"])
    d["possibly_lost_in_transit"] = possibly_lost
    return {"found": True, "order": d}


def check_return_eligibility(order_id: str, item_id: str, reason: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    conn.close()
    if not row:
        return {"eligible": False, "message": f"No order found with id {order_id}."}
    order = row_to_dict(row)
    if order["status"] != "delivered":
        return {"eligible": False, "message": f"Order status is '{order['status']}'. Only delivered orders can be returned."}

    delivery_date = datetime.fromisoformat(order["delivery_date"])
    days_since_delivery = (datetime.now() - delivery_date).days

    extended_reasons = ("defective", "damaged", "wrong_item")
    window = 45 if reason in extended_reasons else 30

    items = json.loads(order["items_json"])
    item_ids = [i["item_id"] for i in items]
    if item_id not in item_ids:
        return {"eligible": False, "message": f"Item {item_id} not found in order {order_id}."}

    eligible = days_since_delivery <= window
    return {
        "eligible": eligible,
        "days_since_delivery": days_since_delivery,
        "window_days": window,
        "message": (
            f"Eligible for return under the {window}-day window ({reason})."
            if eligible else
            f"Not eligible: {days_since_delivery} days have passed, window is {window} days for reason '{reason}'."
        ),
    }


def initiate_return(order_id: str, item_id: str, reason: str):
    elig = check_return_eligibility(order_id, item_id, reason)
    if not elig["eligible"]:
        return {"created": False, "reason_denied": elig["message"]}

    return_id = f"RET{uuid.uuid4().hex[:8].upper()}"
    conn = get_connection()
    conn.execute(
        "INSERT INTO returns (return_id, order_id, item_id, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (return_id, order_id, item_id, reason, "approved", datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    free_shipping_reasons = ("defective", "damaged", "wrong_item", "size_fit")
    return {
        "created": True,
        "return_id": return_id,
        "prepaid_label_issued": reason in free_shipping_reasons,
        "message": f"Return {return_id} created for order {order_id}, item {item_id}, reason '{reason}'.",
    }


def _recent_refund_count(order_id: str, customer_id: str) -> int:
    conn = get_connection()
    row = conn.execute(
        """SELECT COUNT(*) as c FROM refunds
           JOIN orders ON refunds.order_id = orders.order_id
           WHERE orders.customer_id = ? AND refunds.created_at >= ?""",
        (customer_id, (datetime.now() - timedelta(days=30)).isoformat()),
    ).fetchone()
    conn.close()
    return row["c"] if row else 0


def process_refund(order_id: str, amount: float, reason: str, has_associated_return: bool):
    conn = get_connection()
    order_row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if not order_row:
        conn.close()
        return {"processed": False, "message": f"No order found with id {order_id}."}
    customer_id = order_row["customer_id"]
    conn.close()

    recent_count = _recent_refund_count(order_id, customer_id)
    requires_approval, explanation = refund_requires_human_approval(
        amount, has_associated_return, reason, recent_count
    )

    refund_id = f"RFD{uuid.uuid4().hex[:8].upper()}"
    status = "pending_human_approval" if requires_approval else "approved"

    conn = get_connection()
    conn.execute(
        """INSERT INTO refunds (refund_id, order_id, amount, reason, status, requires_human_approval, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (refund_id, order_id, amount, reason, status, int(requires_approval), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    return {
        "processed": not requires_approval,
        "refund_id": refund_id,
        "status": status,
        "requires_human_approval": requires_approval,
        "explanation": explanation,
        "auto_approve_limit": REFUND_AUTO_APPROVE_LIMIT,
    }


def cancel_order(order_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if not row:
        conn.close()
        return {"cancelled": False, "message": f"No order found with id {order_id}."}

    if row["status"] != "processing":
        conn.close()
        return {
            "cancelled": False,
            "message": f"Order status is '{row['status']}'. Only orders still in 'processing' can be self-service cancelled.",
        }

    conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order_id,))
    conn.commit()
    conn.close()
    return {"cancelled": True, "message": f"Order {order_id} cancelled and full refund issued automatically."}


def search_policy(query: str):
    results = search_policies(query, k=3)
    return {"results": results}


def escalate_to_human(reason: str, summary: str, order_id: str = None):
    escalation_id = f"ESC{uuid.uuid4().hex[:8].upper()}"
    conn = get_connection()
    conn.execute(
        "INSERT INTO escalations (escalation_id, order_id, reason, summary, created_at) VALUES (?, ?, ?, ?, ?)",
        (escalation_id, order_id, reason, summary, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return {
        "escalated": True,
        "escalation_id": escalation_id,
        "message": "Conversation flagged for human agent handoff with full context attached.",
    }


# ---------------------------------------------------------------------------
# Anthropic tool-use schemas
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "get_customer_orders",
        "description": "Look up all orders belonging to a customer by their customer_id.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "track_order",
        "description": "Get the current status, tracking number, carrier and delivery dates for a single order.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "check_return_eligibility",
        "description": "Check whether a specific item in an order is eligible for return given a reason code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "item_id": {"type": "string"},
                "reason": {
                    "type": "string",
                    "enum": ["defective", "damaged", "wrong_item", "size_fit", "no_longer_needed"],
                },
            },
            "required": ["order_id", "item_id", "reason"],
        },
    },
    {
        "name": "initiate_return",
        "description": "Create a return request for an item after confirming eligibility. Issues a prepaid shipping label when the reason qualifies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "item_id": {"type": "string"},
                "reason": {
                    "type": "string",
                    "enum": ["defective", "damaged", "wrong_item", "size_fit", "no_longer_needed"],
                },
            },
            "required": ["order_id", "item_id", "reason"],
        },
    },
    {
        "name": "process_refund",
        "description": (
            "Process a refund for an order. Automatically enforces policy: refunds under the auto-approval "
            "limit with a valid reason are approved instantly; larger or unusual refunds are marked "
            "pending_human_approval instead of being paid out. Always call this rather than promising a "
            "refund yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number"},
                "reason": {
                    "type": "string",
                    "enum": ["defective", "damaged", "wrong_item", "missing_item", "delivery_delay_credit", "no_longer_needed", "other"],
                },
                "has_associated_return": {
                    "type": "boolean",
                    "description": "True if this refund follows an initiate_return call for the same order/item.",
                },
            },
            "required": ["order_id", "amount", "reason", "has_associated_return"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel an order that has not yet shipped (status must be 'processing'). Issues an automatic full refund.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "search_policy",
        "description": "Search ORD Bot's policy documents (returns, refunds, shipping, cancellations) for the passage relevant to the customer's question. Use this before making any policy claim you're not certain about.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Hand the conversation off to a human agent. Use whenever policy requires human approval "
            "(large refunds, refunds without a return, suspected fraud, possibly-lost shipments), the "
            "customer asks for a human, they are very frustrated, or you are not confident how to resolve "
            "the issue safely. Always include a full summary so the human doesn't need the customer to repeat themselves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short machine-readable reason code, e.g. 'refund_over_limit', 'customer_requested', 'possible_fraud', 'possibly_lost_shipment', 'low_confidence'."},
                "summary": {"type": "string", "description": "A complete handoff summary: what the customer wants, what has been checked/found, and what remains to be resolved."},
                "order_id": {"type": "string"},
            },
            "required": ["reason", "summary"],
        },
    },
]

TOOL_FUNCTIONS = {
    "get_customer_orders": get_customer_orders,
    "track_order": track_order,
    "check_return_eligibility": check_return_eligibility,
    "initiate_return": initiate_return,
    "process_refund": process_refund,
    "cancel_order": cancel_order,
    "search_policy": search_policy,
    "escalate_to_human": escalate_to_human,
}


def execute_tool(name: str, tool_input: dict):
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool '{name}'"}
    try:
        return fn(**tool_input)
    except Exception as e:
        return {"error": str(e)}
