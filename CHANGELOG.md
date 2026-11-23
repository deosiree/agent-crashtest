# Changelog

## Second public snapshot

A harness fix and tests. No experiment, result or contract text changed: `docs/contract.md` is still v0.3, and every results file is unchanged.

**Fixed: the fault proxy's reads of the sink.** The connection-drop proxy (`src/crashtest/faults/drop_proxy.py`) waits for a refund to commit before it cuts a connection, by polling the sink.
- It now keeps one HTTP client to the sink for its whole life. Before, it created a client for each commit wait; that took about 150 ms, and up to about 750 ms on a loaded machine, and could lose the race with a client's timeout.
- That client drops idle connections after 1 second (`SINK_KEEPALIVE_S`), before the sink's server does (5 seconds). Without this, a read could reuse a connection the server had just closed.
- A read that fails at the connection level is retried once. This is a second race, between a read and the sink closing an idle connection; it was found by code review, not seen in a run. A timeout or an HTTP error status is not retried, so a slow or failing sink still shows as one.

**New trial field: `sink_retries`.** In the trial records of experiments 002 and 007: the number of sink reads the proxy retried during the trial, or `null` when the trial ran without the proxy. Results files written before this change do not have the field, and experiment 003's records (`single_call`) do not carry it yet, although its trials go through the proxy.

**Tests.**
- `tests/test_drop_proxy_sink.py`: the retry, what is not retried, the idle expiry, and a read across the expiry against the real sink.
- `tests/test_runner_smoke.py`: one trial through the 003, 004 and 005 runners' real code paths. The 004 and 005 tests run only when their subject environments are named (see the module's docstring).
- `tests/test_sink.py`: the fault-delay test allows 50 ms of slack, because a Windows timer can fire up to one clock tick (about 15.6 ms) early.

**Evidence.** Re-runs of experiments 002 and 007 with this fix, on the maintainer's development tree, matched their recorded outcomes in every cell, at 5 trials per cell (the recorded runs used 20). That shows no outcome changed at that sample size; it does not show either race is fixed, and rare changes are not ruled out. The evidence for the keep-alive fix is `tests/test_drop_proxy_sink.py` (the retry, what is not retried, and the idle expiry). No test measures the client-creation delay directly.
