"""Subject: a pydantic-ai agent made durable with DBOS, refunding one order.

Driven by `crashtest.experiment.dbos_crash` (experiment 005).

The model is scripted (`FunctionModel`): its first call returns a tool call to
`issue_refund`, its second a final message. No model API is involved.

`--tool-mode` decides how the tool reaches the sink:
  plain           the HTTP call is made directly in the tool function, which
                  pydantic-ai runs in workflow code (function tools are not
                  wrapped in steps)
  step            the tool function calls a `@DBOS.step`, so DBOS records its result
  child_workflow  the tool is marked to run as a DBOS child workflow

`--key-strategy` decides the idempotency key the tool sends:
  none          no key
  tool_call_id  the id of the model's tool call
  workflow_id   the id of the DBOS workflow the tool body runs in
  business      derived from the order ("refund-<order_id>")

`--hold-at model2` makes the process announce `AT_HOLD` and wait just before the
second model call, so the harness can kill it after the tool has returned.

Run 1:        ... --order-id X --workflow-id W
After a kill: ... --order-id X --workflow-id W --resume
On --resume the script only launches DBOS, which recovers the pending workflows
of its executor (`--executor-id`) by itself, and waits for the workflow's result. It makes no decision of its own
about whether to run anything again. The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import warnings
from importlib import metadata
from typing import Any

import httpx
from dbos import DBOS, DBOSConfig, SetWorkflowID
from pydantic_ai import Agent, RunContext
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

warnings.filterwarnings("ignore")

ARGS: argparse.Namespace
COUNTS = {"model_calls": 0, "tool_calls": 0, "sink_posts": 0}
KEYS_SENT: list[str | None] = []


def scripted_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    COUNTS["model_calls"] += 1
    tool_returned = any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts)
    if tool_returned:
        if ARGS.hold_at == "model2":
            print("AT_HOLD", flush=True)
            time.sleep(600)  # the harness kills the process here
        return ModelResponse(parts=[TextPart("The refund has been issued.")])
    return ModelResponse(
        parts=[
            ToolCallPart(
                "issue_refund", {"order_id": ARGS.order_id, "amount_cents": ARGS.amount_cents}
            )
        ]
    )


def _key(order_id: str, tool_call_id: str | None) -> str | None:
    if ARGS.key_strategy == "tool_call_id":
        return f"call-{tool_call_id}"
    if ARGS.key_strategy == "workflow_id":
        return f"wf-{DBOS.workflow_id}"
    if ARGS.key_strategy == "business":
        return f"refund-{order_id}"
    return None


def _post_refund(order_id: str, amount_cents: int, key: str | None) -> str:
    COUNTS["sink_posts"] += 1
    KEYS_SENT.append(key)
    response = httpx.post(
        f"{ARGS.sink_url}/refunds",
        json={"order_id": order_id, "amount_cents": amount_cents},
        headers={"Idempotency-Key": key} if key else {},
        timeout=ARGS.http_timeout,
    )
    response.raise_for_status()
    return json.dumps(response.json())


def _make_step() -> Any:
    """The step wrapper. Retries are off unless --step-retries (DBOS's default is off)."""
    if ARGS.step_retries:
        return DBOS.step(
            name="post_refund", retries_allowed=True, max_attempts=3, interval_seconds=0.5
        )(_post_refund)
    return DBOS.step(name="post_refund")(_post_refund)


def build_agent() -> Agent[None, str]:
    """Build the agent for the chosen tool mode. Called after the arguments are parsed."""
    post_refund_step = _make_step()
    toolset: FunctionToolset[None] = FunctionToolset(id="refunds")
    description = "Refund an order. This moves money and must not be repeated for the same order."

    if ARGS.tool_mode == "child_workflow":
        # pydantic-ai supports child workflows only for async tools in a toolset with an id.
        async def issue_refund_child(
            ctx: RunContext[None], order_id: str, amount_cents: int
        ) -> str:
            COUNTS["tool_calls"] += 1
            key = _key(order_id, ctx.tool_call_id)
            return await asyncio.to_thread(_post_refund, order_id, amount_cents, key)

        toolset.add_function(
            issue_refund_child,
            name="issue_refund",
            description=description,
            metadata={"dbos": {"child_workflow": {}}},
        )
    else:

        def issue_refund(ctx: RunContext[None], order_id: str, amount_cents: int) -> str:
            COUNTS["tool_calls"] += 1
            key = _key(order_id, ctx.tool_call_id)
            if ARGS.tool_mode == "step":
                return post_refund_step(order_id, amount_cents, key)
            return _post_refund(order_id, amount_cents, key)

        toolset.add_function(issue_refund, name="issue_refund", description=description)

    return Agent(
        FunctionModel(scripted_model),
        name="refund_agent",
        toolsets=[toolset],
        capabilities=[DBOSDurability()],
    )


async def run() -> dict[str, Any]:
    report: dict[str, Any] = {
        "client": f"pydantic-ai-slim {metadata.version('pydantic-ai-slim')}, "
        f"dbos {metadata.version('dbos')}",
        "tool_mode": ARGS.tool_mode,
        "system_database": ARGS.system_database_url.split(":", 1)[0],
    }
    agent = build_agent()

    @DBOS.workflow(name="refund_workflow")
    async def refund_workflow(order_id: str) -> str:
        result = await agent.run(f"Refund order {order_id}.")
        return str(result.output)

    config: DBOSConfig = {
        "name": "crashtest_pydantic_ai",
        "system_database_url": ARGS.system_database_url,
    }
    if ARGS.executor_id:
        # DBOS recovers the pending workflows of its own executor only. One
        # executor per trial keeps a workflow left behind by another trial out.
        config["executor_id"] = ARGS.executor_id
        report["executor_id"] = ARGS.executor_id
    DBOS(config=config)
    DBOS.launch()  # recovers this executor's pending workflows
    try:
        if ARGS.resume:
            report["path"] = "recovered_by_dbos"
            status = await DBOS.get_workflow_status_async(ARGS.workflow_id)
            report["status_at_resume"] = getattr(status, "status", None)
            handle = await DBOS.retrieve_workflow_async(ARGS.workflow_id)
            report["output"] = await asyncio.wait_for(handle.get_result(), timeout=ARGS.wait)
        else:
            report["path"] = "first_run"
            with SetWorkflowID(ARGS.workflow_id):
                report["output"] = await refund_workflow(ARGS.order_id)
        report["outcome"] = "ok"
        report["answered"] = bool(report.get("output"))
    except BaseException as exc:  # noqa: BLE001 - the report must say what was raised
        report["outcome"] = "raised"
        report["detail"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        try:
            final = await DBOS.get_workflow_status_async(ARGS.workflow_id)
            report["final_status"] = getattr(final, "status", None)
            report["recovery_attempts"] = getattr(final, "recovery_attempts", None)
        except Exception as exc:  # noqa: BLE001
            report["final_status"] = f"unavailable: {type(exc).__name__}"
        DBOS.destroy()
    report.update(COUNTS)
    report["keys_sent"] = KEYS_SENT
    return report


def main() -> int:
    global ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument("--system-database-url", required=True)
    parser.add_argument("--sink-url", required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--executor-id", default="", help="default: DBOS's own ('local')")
    parser.add_argument("--amount-cents", type=int, default=2500)
    parser.add_argument("--tool-mode", choices=("plain", "step", "child_workflow"), default="plain")
    parser.add_argument(
        "--key-strategy",
        choices=("none", "tool_call_id", "workflow_id", "business"),
        default="none",
    )
    parser.add_argument("--http-timeout", type=float, default=600.0)
    parser.add_argument("--step-retries", action="store_true")
    parser.add_argument("--hold-at", choices=("", "model2"), default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--wait", type=float, default=60.0)
    ARGS = parser.parse_args()
    print(json.dumps(asyncio.run(run())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
