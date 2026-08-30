"""
database.py
Lightweight SQLite-backed order/customer store, seeded from data/seed_data.json.
In a real system this module would be replaced by calls to the actual
order-management / CRM APIs. It exists here purely to make the demo
self-contained and runnable without external services.
"""

import json
import sqlite3
import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "resolveai.db")
SEED_PATH = os.path.join(BASE_DIR, "data", "seed_data.json")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(force: bool = False):
    if force and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT,
            loyalty_tier TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT,
            status TEXT,
            order_date TEXT,
            delivery_date TEXT,
            expected_delivery_date TEXT,
            tracking_number TEXT,
            carrier TEXT,
            total_amount REAL,
            currency TEXT,
            items_json TEXT,
            last_tracking_update TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS returns (
            return_id TEXT PRIMARY KEY,
            order_id TEXT,
            item_id TEXT,
            reason TEXT,
            status TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS refunds (
            refund_id TEXT PRIMARY KEY,
            order_id TEXT,
            amount REAL,
            reason TEXT,
            status TEXT,
            requires_human_approval INTEGER,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS escalations (
            escalation_id TEXT PRIMARY KEY,
            order_id TEXT,
            reason TEXT,
            summary TEXT,
            created_at TEXT
        )
    """)

    # seed only if empty
    cur.execute("SELECT COUNT(*) as c FROM customers")
    if cur.fetchone()["c"] == 0:
        with open(SEED_PATH) as f:
            seed = json.load(f)

        for c in seed["customers"]:
            cur.execute(
                "INSERT INTO customers (customer_id, name, email, loyalty_tier) VALUES (?, ?, ?, ?)",
                (c["customer_id"], c["name"], c["email"], c["loyalty_tier"]),
            )

        for o in seed["orders"]:
            now = datetime.now()
            order_date = (now - timedelta(days=o["order_days_ago"])).strftime("%Y-%m-%d")

            delivery_date = None
            if o.get("delivery_days_ago") is not None:
                delivery_date = (now - timedelta(days=o["delivery_days_ago"])).strftime("%Y-%m-%d")

            expected_delivery_date = None
            if o.get("expected_delivery_days_from_now") is not None:
                expected_delivery_date = (now + timedelta(days=o["expected_delivery_days_from_now"])).strftime("%Y-%m-%d")

            # Tracking defaults to "last updated when the order was placed" unless
            # overridden — most orders update tracking regularly, so this only
            # matters for the orders we deliberately want to look stale.
            tracking_days_ago = o.get("last_tracking_update_days_ago", o["order_days_ago"])
            last_tracking_update = (now - timedelta(days=tracking_days_ago)).strftime("%Y-%m-%d")

            cur.execute(
                """INSERT INTO orders
                (order_id, customer_id, status, order_date, delivery_date, expected_delivery_date,
                 tracking_number, carrier, total_amount, currency, items_json, last_tracking_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    o["order_id"], o["customer_id"], o["status"], order_date,
                    delivery_date, expected_delivery_date,
                    o.get("tracking_number"), o.get("carrier"),
                    o["total_amount"], o["currency"], json.dumps(o["items"]),
                    last_tracking_update,
                ),
            )
        conn.commit()

    conn.close()


def row_to_dict(row):
    return dict(row) if row else None


if __name__ == "__main__":
    init_db(force=True)
    print(f"Database initialized at {DB_PATH}")
