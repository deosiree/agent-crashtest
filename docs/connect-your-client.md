# Point your own MCP client at the refund tool

This guide is for testing a client, framework or agent of your own against the same fault the experiments use. The fault: the sink commits a refund, then holds its response past your client's timeout, so the client never hears back. What decides the result is how many refunds the sink holds afterwards, not what your client says.

## 1. Start the stack

**With Docker:**

```sh
docker compose up --build -d --wait
```

The sink listens on `http://localhost:8101`. The MCP server, stateless JSON over streamable HTTP, is at `http://localhost:8102/mcp`. Three more MCP servers with other session and response settings are on ports 8103 to 8105; see `docker-compose.yml`.

**Without Docker**, from Python, with the project installed (`pip install -e ".[dev]"`):

```python
from crashtest.experiment.local_stack import local_stack

with local_stack() as stack:
    print("sink:", stack.sink_url, "mcp:", stack.mcp_url)
    input("Stack running; press Enter to stop.")
```

It starts PostgreSQL, the sink and the MCP server on free ports, and prints them. The steps below use the Docker ports; use the printed URLs instead if you start it this way.

## 2. The tool

The MCP server has one tool, `issue_refund`:

| Argument | Type | Meaning |
|---|---|---|
| `order_id` | string | The order to refund. Use a fresh one per trial, such as `ord-<uuid>` |
| `amount_cents` | integer | The amount, for example `2500` |
| `idempotency_key` | string, optional | A caller-chosen key for this one refund. A call that repeats the key gets the stored result back instead of a second refund |

The server adds no deduplication of its own. It passes the key to the sink, which enforces it in the database.

## 3. Run one trial

1. **Pick a fresh order ID**, for example `ord-3f2a…`.
2. **Arm the fault for that order.** The next refund for it commits, then its response is held for `delay_s` seconds:

   ```sh
   curl -X PUT http://localhost:8101/control/faults \
     -H "Content-Type: application/json" \
     -d '{"order_id": "ord-3f2a", "delay_s": 5, "times": 1}'
   ```

3. **Call `issue_refund` from your client** with that order ID, and set a client timeout shorter than `delay_s` (2 seconds against 5, as the experiments do). Let your client do whatever it does by default: retry, give up, or report.
4. **Wait out the delay**, then **read the truth from the sink**:

   ```sh
   curl "http://localhost:8101/refunds?order_id=ord-3f2a"
   ```

   It returns `refunds`, the effects committed, and `requests`, every refund request the sink received for the order. One refund is right; two is a duplicate.

5. **Compare with what your client reported.** If it said "refunded" and the sink holds two refunds, its report contradicts the sink: that is property RA in the [contract](contract.md), recorded as `report_matches_sink`.

To judge the trial with the project's own rules:

```python
from crashtest.checker import classify, report_matches_sink

effects = 2  # len(refunds) from the sink
print(classify(effects=effects, intended=1, status="completed",
               payload_matches=True, confirmed=True))          # duplicate
print(report_matches_sink(effects=effects, intended=1, report="done"))  # False
```

`status` is how your client's run ended: `completed`, `unknown` (it reported that it does not know) or `failed`. `report` is what it told its caller: `done`, `unknown`, `not_done` or `none`. The [contract](contract.md), section 3, defines both.

## 4. A worked example

[`subjects/mcp_sdk.py`](../subjects/mcp_sdk.py) is the reference `mcp` client wrapped this way. It makes one call, never retries itself, and prints one JSON line: `ok`, `error` or `raised`. The other files in `subjects/` do the same for CrewAI, Microsoft agent-framework, LangGraph and pydantic-ai on DBOS. To add your client to the experiment that drives them (`python -m crashtest.experiment.single_call`), save a script with the same arguments (`--mcp-url`, `--order-id`, `--amount-cents`, `--timeout`) and the same one-line JSON report as `subjects/<name>.py`, then pass `--subject <name>=<path to the Python that has your client installed>`. See [experiment 003](experiments/003-mcp-client-libraries.md) for the full command.

## Reset between runs

```sh
curl -X POST http://localhost:8101/control/reset
```

This empties the sink's refunds, request log and armed faults, and restarts their IDs. Use it only on a test stack.

Checked on 20 November 2026 on Windows with the local stack: the armed fault held the response, the reference client timed out after 2 seconds (`raised`), and the sink held one refund.
