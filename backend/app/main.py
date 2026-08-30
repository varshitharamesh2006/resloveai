"""
main.py
FastAPI entrypoint for ORD Bot. Exposes a simple chat API used by the
frontend. Conversation state is kept in-memory per conversation_id for this
demo (swap for Redis/DB in production).
"""

import uuid
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import init_db, get_connection
from agent import run_turn

app = FastAPI(title="ORD Bot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store: conversation_id -> {"messages": [...], "customer_id": str}
SESSIONS = {}


@app.on_event("startup")
def startup():
    init_db()


class StartRequest(BaseModel):
    customer_id: str


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


@app.post("/api/start")
def start_conversation(req: StartRequest):
    conn = get_connection()
    row = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (req.customer_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown customer_id {req.customer_id}")

    conversation_id = str(uuid.uuid4())
    SESSIONS[conversation_id] = {"messages": [], "customer_id": req.customer_id}
    return {
        "conversation_id": conversation_id,
        "customer_name": row["name"],
        "loyalty_tier": row["loyalty_tier"],
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    session = SESSIONS.get(req.conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown conversation_id. Call /api/start first.")

    session["messages"].append({"role": "user", "content": req.message})

    result = run_turn(session["messages"], session["customer_id"])
    session["messages"] = result.messages

    return {
        "reply": result.reply_text,
        "escalated": result.escalated,
        "escalation_info": result.escalation_info,
        "tool_trace": result.tool_trace,
    }


@app.get("/api/customers")
def list_customers():
    """Convenience endpoint so the demo frontend can offer a customer picker."""
    conn = get_connection()
    rows = conn.execute("SELECT customer_id, name, loyalty_tier FROM customers").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/health")
def health():
    return {"status": "ok"}
