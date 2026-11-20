"""Subject: a LangGraph ReAct agent with a PostgreSQL checkpointer that refunds one order.

The model is scripted, so no API key or network model is involved:
  1st model call  -> a tool call to `issue_refund`, with a fresh tool-call id, as real
                     models produce
  2nd model call  -> a final message

The tool posts to the sink. `--key-strategy` decides its idempotency key:
  none          no key
  tool_call_id  the id of the model's tool call
  business      derived from the order ("refund-<order_id>")

`--hold-at model2` makes the process announce `AT_HOLD` and wait just before the
second model call, so the harness can kill it after the tool has returned.

Run 1:        ... --order-id X --thread-id T
After a kill: ... --order-id X --thread-id T --resume

On --resume the script does what an application would do after a restart: if the
thread has a checkpoint with work pending it resumes; if it has no checkpoint it
submits the request again; if the thread already finished it does nothing.
The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from importlib import metadata
from typing import Annotated, Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.prebuilt import create_react_agent

ARGS: argparse.Namespace
MODEL_CALLS = 0
TOOL_CALLS = 0


class ScriptedModel(BaseChatModel):
    """Calls `issue_refund` once, then finishes."""

    order_id: str
    amount_cents: int
    hold_at: str = ""

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        global MODEL_CALLS
        MODEL_CALLS += 1
        if any(isinstance(m, ToolMessage) for m in messages):
            if self.hold_at == "model2":
                print("AT_HOLD", flush=True)
                time.sleep(600)  # the harness kills the process here
            message = AIMessage(content="The refund has been issued.")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "issue_refund",
                        "args": {"order_id": self.order_id, "amount_cents": self.amount_cents},
                        "id": f"call_{uuid.uuid4().hex}",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def issue_refund(
    order_id: str, amount_cents: int, tool_call_id: Annotated[str, InjectedToolCallId]
) -> str:
    """Refund an order. This moves money and must not be repeated for the same order."""
    global TOOL_CALLS
    TOOL_CALLS += 1
    headers = {}
    if ARGS.key_strategy == "tool_call_id":
        headers["Idempotency-Key"] = tool_call_id
    elif ARGS.key_strategy == "business":
        headers["Idempotency-Key"] = f"refund-{order_id}"
    response = httpx.post(
        f"{ARGS.sink_url}/refunds",
        json={"order_id": order_id, "amount_cents": amount_cents},
        headers=headers,
        timeout=600.0,
    )
    response.raise_for_status()
    return json.dumps(response.json())


def main() -> None:
    global ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--sink-url", required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--amount-cents", type=int, default=2500)
    parser.add_argument("--durability", choices=("sync", "async", "exit"), default="async")
    parser.add_argument("--key-strategy", choices=("none", "tool_call_id", "business"), default="none")
    parser.add_argument("--hold-at", choices=("", "model2"), default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--setup-only", action="store_true", help="create checkpoint tables and exit")
    ARGS = parser.parse_args()

    report: dict[str, Any] = {
        "client": f"langgraph {metadata.version('langgraph')}, "
        f"checkpoint-postgres {metadata.version('langgraph-checkpoint-postgres')}",
        "durability": ARGS.durability,
    }
    config = {"configurable": {"thread_id": ARGS.thread_id}}
    request = {"messages": [HumanMessage(content=f"Refund order {ARGS.order_id}.")]}

    with PostgresSaver.from_conn_string(ARGS.database_url) as saver:
        if ARGS.setup_only:
            saver.setup()
            print(json.dumps({"outcome": "setup"}))
            return
        model = ScriptedModel(order_id=ARGS.order_id, amount_cents=ARGS.amount_cents, hold_at=ARGS.hold_at)
        agent = create_react_agent(model, [issue_refund], checkpointer=saver)

        if not ARGS.resume:
            report["path"] = "first_run"
            payload: Any = request
        else:
            state = agent.get_state(config)
            has_checkpoint = bool(state.config.get("configurable", {}).get("checkpoint_id"))
            report["resume_saw"] = {
                "has_checkpoint": has_checkpoint,
                "next": list(state.next),
                "messages": [type(m).__name__ for m in state.values.get("messages", [])],
            }
            messages = state.values.get("messages", [])
            answered = bool(messages) and isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls
            if has_checkpoint and state.next:
                report["path"] = "resumed_from_checkpoint"
                payload = None
            elif not has_checkpoint:
                report["path"] = "resubmitted_no_checkpoint"
                payload = request
            elif answered:
                report["path"] = "already_finished"
                payload = "skip"
            else:
                # The checkpoint lists nothing to run, yet the agent never answered.
                # Ask LangGraph to continue anyway and record what it does.
                report["path"] = "nothing_pending_but_unanswered"
                payload = None

        try:
            if payload != "skip":
                agent.invoke(payload, config, durability=ARGS.durability)
            report["outcome"] = "ok"
        except BaseException as exc:  # noqa: BLE001 - the report must say what was raised
            report["outcome"] = "raised"
            report["detail"] = f"{type(exc).__name__}: {str(exc)[:200]}"

        final = agent.get_state(config)
        final_messages = final.values.get("messages", [])
        report["final_messages"] = [type(m).__name__ for m in final_messages]
        report["final_next"] = list(final.next)
        report["answered"] = (
            bool(final_messages)
            and isinstance(final_messages[-1], AIMessage)
            and not final_messages[-1].tool_calls
        )

    report["model_calls"] = MODEL_CALLS
    report["tool_calls"] = TOOL_CALLS
    print(json.dumps(report))


if __name__ == "__main__":
    sys.exit(main())
