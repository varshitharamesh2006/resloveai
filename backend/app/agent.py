"""
agent.py
The ORD Bot agent loop: takes a conversation, calls Claude with the tool
definitions from tools.py, executes any tool calls the model makes, feeds
the results back, and repeats until the model produces a final text answer
or calls escalate_to_human.
"""

import os
import json
from anthropic import Anthropic

from tools import TOOLS, execute_tool

MODEL = "claude-sonnet-4-6"
MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """You are ORD Bot, an AI customer support resolution agent for an e-commerce company.

Your job is to actually RESOLVE the customer's issue end-to-end, not just answer questions:
- Look up real order/customer data using your tools before saying anything specific about an order.
- Ground every policy claim (return windows, refund rules, cancellation rules) in a search_policy call.
  Never state a policy detail from memory alone if a tool exists to check it.
- Take the permitted action (initiate_return, process_refund, cancel_order) when it's clearly justified,
  rather than just telling the customer what they could do.
- If a refund or action comes back as pending_human_approval, or any escalation criterion from the
  escalation policy is met, call escalate_to_human with a full summary. Do not tell the customer an
  action succeeded if the tool result says it requires human approval or was denied.
- Be honest about denials (e.g. return window passed) but explain the reason clearly and offer any
  legitimate alternative the policy allows.
- Keep responses concise, warm, and specific (use real order numbers, dates, amounts once you have them).
- Never invent order details, tracking numbers, policy terms, or refund amounts. If you don't have the
  information, look it up or escalate.
- If you're ever unsure which policy applies or the case has conflicting signals, escalate rather than guess.

You are speaking directly with the customer in a live chat.
"""


class AgentResponse:
    def __init__(self, reply_text, escalated, escalation_info, tool_trace, messages):
        self.reply_text = reply_text
        self.escalated = escalated
        self.escalation_info = escalation_info
        self.tool_trace = tool_trace
        self.messages = messages  # updated running message history


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return Anthropic(api_key=api_key)


def run_turn(messages: list, customer_id: str) -> AgentResponse:
    """
    messages: running list of {"role": ..., "content": ...} in Anthropic format,
              already including the new user turn.
    customer_id: injected as context so the agent doesn't have to ask for it
                 (in production this comes from the authenticated session).
    """
    client = _client()
    tool_trace = []
    escalated = False
    escalation_info = None

    system = SYSTEM_PROMPT + f"\n\nThe authenticated customer's customer_id is: {customer_id}"

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=messages,
            tools=TOOLS,
        )

        if response.stop_reason != "tool_use":
            final_text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            messages.append({"role": "assistant", "content": response.content})
            return AgentResponse(final_text, escalated, escalation_info, tool_trace, messages)

        # model wants to use one or more tools
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = execute_tool(block.name, block.input)
            tool_trace.append({"tool": block.name, "input": block.input, "result": result})

            if block.name == "escalate_to_human":
                escalated = True
                escalation_info = {**block.input, **result}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

    # safety valve: too many tool iterations, force an answer
    messages.append({
        "role": "user",
        "content": "Please summarize the resolution or escalate to a human now.",
    })
    response = client.messages.create(
        model=MODEL, max_tokens=1024, system=system, messages=messages, tools=TOOLS,
    )
    final_text = "".join(block.text for block in response.content if block.type == "text")
    messages.append({"role": "assistant", "content": response.content})
    return AgentResponse(final_text, escalated, escalation_info, tool_trace, messages)
