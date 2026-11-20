"""The sink's own behaviour, tested directly over HTTP against real PostgreSQL."""

from __future__ import annotations

import asyncio
import time
import uuid


async def _truth(sink, order_id: str) -> dict:
    r = await sink.get("/refunds", params={"order_id": order_id})
    r.raise_for_status()
    return r.json()


async def test_health(sink):
    r = await sink.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_without_key_every_request_is_a_new_effect(sink, order_id):
    body = {"order_id": order_id, "amount_cents": 2500}
    first = await sink.post("/refunds", json=body)
    second = await sink.post("/refunds", json=body)

    assert first.status_code == second.status_code == 200
    assert first.json()["refund_id"] != second.json()["refund_id"]
    truth = await _truth(sink, order_id)
    assert len(truth["refunds"]) == 2
    assert [r["outcome"] for r in truth["requests"]] == ["created", "created"]


async def test_same_key_replays_the_original_refund(sink, order_id):
    body = {"order_id": order_id, "amount_cents": 2500}
    headers = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    first = await sink.post("/refunds", json=body, headers=headers)
    second = await sink.post("/refunds", json=body, headers=headers)

    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert first.json()["refund_id"] == second.json()["refund_id"]
    truth = await _truth(sink, order_id)
    assert len(truth["refunds"]) == 1
    assert [r["outcome"] for r in truth["requests"]] == ["created", "replayed"]


async def test_same_key_with_different_body_is_rejected(sink, order_id):
    headers = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    first = await sink.post(
        "/refunds", json={"order_id": order_id, "amount_cents": 2500}, headers=headers
    )
    second = await sink.post(
        "/refunds", json={"order_id": order_id, "amount_cents": 9900}, headers=headers
    )

    assert first.status_code == 200
    assert second.status_code == 409
    truth = await _truth(sink, order_id)
    assert len(truth["refunds"]) == 1
    assert truth["refunds"][0]["amount_cents"] == 2500
    assert [r["outcome"] for r in truth["requests"]] == ["created", "conflict"]


async def test_each_request_is_linked_to_the_refund_it_made_or_was_answered_with(sink, order_id):
    body = {"order_id": order_id, "amount_cents": 2500}
    keyed = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    await sink.post("/refunds", json=body)  # no key: one refund
    await sink.post("/refunds", json=body)  # no key: a second refund
    await sink.post("/refunds", json=body, headers=keyed)
    await sink.post("/refunds", json=body, headers=keyed)  # replay

    truth = await _truth(sink, order_id)
    ids = [r["id"] for r in truth["refunds"]]
    assert [q["refund_id"] for q in truth["requests"]] == [ids[0], ids[1], ids[2], ids[2]]


async def test_a_key_reused_for_another_order_is_a_conflict_logged_under_the_new_order(
    sink, order_id
):
    # The unique index is on the key alone, not on (order, key). This test pins
    # down what that means: the refund stays with the first order, and the
    # conflict is logged under the second.
    other = f"{order_id}-other"
    headers = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    first = await sink.post(
        "/refunds", json={"order_id": order_id, "amount_cents": 2500}, headers=headers
    )
    second = await sink.post(
        "/refunds", json={"order_id": other, "amount_cents": 2500}, headers=headers
    )

    assert first.status_code == 200
    assert second.status_code == 409
    mine, theirs = await _truth(sink, order_id), await _truth(sink, other)
    assert len(mine["refunds"]) == 1 and theirs["refunds"] == []
    assert [q["outcome"] for q in theirs["requests"]] == ["conflict"]
    assert theirs["requests"][0]["refund_id"] == mine["refunds"][0]["id"]


async def test_concurrent_requests_with_one_key_create_one_effect(sink, order_id):
    body = {"order_id": order_id, "amount_cents": 2500}
    headers = {"Idempotency-Key": f"k-{uuid.uuid4()}"}

    responses = await asyncio.gather(
        *[sink.post("/refunds", json=body, headers=headers) for _ in range(25)]
    )

    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["refund_id"] for r in responses}) == 1
    assert sum(r.json()["replayed"] is False for r in responses) == 1
    truth = await _truth(sink, order_id)
    assert len(truth["refunds"]) == 1
    assert len(truth["requests"]) == 25


async def test_fault_commits_the_effect_before_the_response_arrives(sink, order_id):
    delay = 3.0
    r = await sink.put("/control/faults", json={"order_id": order_id, "delay_s": delay, "times": 1})
    assert r.status_code == 200

    started = time.perf_counter()
    pending = asyncio.create_task(
        sink.post("/refunds", json={"order_id": order_id, "amount_cents": 2500})
    )

    # Poll instead of sleeping a fixed time: on a slow machine the commit can
    # take longer than any fixed guess. The refund must appear while the
    # caller is still waiting for its response.
    seen_while_pending = False
    while not pending.done():
        if len((await _truth(sink, order_id))["refunds"]) == 1:
            seen_while_pending = not pending.done()
            break
        await asyncio.sleep(0.05)
    assert seen_while_pending

    response = await pending
    assert response.status_code == 200
    assert time.perf_counter() - started >= delay


async def test_fault_fires_only_the_armed_number_of_times(sink, order_id):
    await sink.put("/control/faults", json={"order_id": order_id, "delay_s": 1.0, "times": 1})
    body = {"order_id": order_id, "amount_cents": 2500}

    await sink.post("/refunds", json=body)  # consumes the fault
    started = time.perf_counter()
    await sink.post("/refunds", json=body)

    assert time.perf_counter() - started < 0.8


async def test_invalid_amount_is_rejected_without_an_effect(sink, order_id):
    r = await sink.post("/refunds", json={"order_id": order_id, "amount_cents": 0})

    assert r.status_code == 422
    assert (await _truth(sink, order_id))["refunds"] == []


async def test_the_log_records_which_request_had_the_fault_applied(sink, order_id):
    await sink.put("/control/faults", json={"order_id": order_id, "delay_s": 0.2, "times": 1})
    body = {"order_id": order_id, "amount_cents": 2500}

    await sink.post("/refunds", json=body)
    await sink.post("/refunds", json=body)

    truth = await _truth(sink, order_id)
    assert [r["fault_applied"] for r in truth["requests"]] == [True, False]


async def test_a_replay_does_not_consume_the_fault(sink, order_id):
    body = {"order_id": order_id, "amount_cents": 2500}
    headers = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    await sink.post("/refunds", json=body, headers=headers)  # creates the effect
    await sink.put("/control/faults", json={"order_id": order_id, "delay_s": 1.0, "times": 1})

    started = time.perf_counter()
    replay = await sink.post("/refunds", json=body, headers=headers)
    replay_elapsed = time.perf_counter() - started

    assert replay.json()["replayed"] is True
    assert replay_elapsed < 0.8  # a replay commits nothing, so nothing is delayed

    # The fault is still armed for the next request that creates an effect.
    started = time.perf_counter()
    await sink.post("/refunds", json=body)
    assert time.perf_counter() - started >= 0.9  # timers can wake slightly early on Windows
    truth = await _truth(sink, order_id)
    assert [r["fault_applied"] for r in truth["requests"]] == [False, False, True]


async def test_a_conflict_does_not_consume_the_fault(sink, order_id):
    headers = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    await sink.post("/refunds", json={"order_id": order_id, "amount_cents": 2500}, headers=headers)
    await sink.put("/control/faults", json={"order_id": order_id, "delay_s": 1.0, "times": 1})

    conflict = await sink.post(
        "/refunds", json={"order_id": order_id, "amount_cents": 9900}, headers=headers
    )
    assert conflict.status_code == 409

    started = time.perf_counter()
    await sink.post("/refunds", json={"order_id": order_id, "amount_cents": 2500})
    assert time.perf_counter() - started >= 0.9  # timers can wake slightly early on Windows
    truth = await _truth(sink, order_id)
    assert [r["outcome"] for r in truth["requests"]] == ["created", "conflict", "created"]
    assert [r["fault_applied"] for r in truth["requests"]] == [False, False, True]


async def test_an_empty_idempotency_key_is_rejected(sink, order_id):
    r = await sink.post(
        "/refunds",
        json={"order_id": order_id, "amount_cents": 2500},
        headers={"Idempotency-Key": ""},
    )

    assert r.status_code == 422
    assert (await _truth(sink, order_id))["refunds"] == []
