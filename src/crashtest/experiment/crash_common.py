"""Helpers shared by the runners that kill a subject process and restart it."""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
from collections.abc import Iterable, Sequence
from itertools import product
from pathlib import Path

import httpx

from crashtest.experiment.single_call import _last_json_line


async def spawn(python: str, script: Path, *args: str) -> asyncio.subprocess.Process:
    """Start a subject script in its own interpreter, with both output streams captured."""
    return await asyncio.create_subprocess_exec(
        python,
        str(script),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def finish(proc: asyncio.subprocess.Process, limit_s: float) -> dict:
    """Wait for a subject to exit and return the JSON report on its last stdout line."""
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=limit_s)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return {"outcome": "hung"}
    report = _last_json_line(stdout.decode(errors="replace"))
    if not report:
        report = {"outcome": "no_report", "stderr_tail": stderr.decode(errors="replace")[-500:]}
    return report


async def wait_for_refund(sink: httpx.AsyncClient, order_id: str, limit_s: float) -> bool:
    """Poll the sink until a refund for this order has committed."""
    deadline = asyncio.get_running_loop().time() + limit_s
    while asyncio.get_running_loop().time() < deadline:
        r = await sink.get("/refunds", params={"order_id": order_id})
        if r.status_code == 200 and r.json()["refunds"]:
            return True
        await asyncio.sleep(0.05)
    return False


async def wait_for_marker(proc: asyncio.subprocess.Process, marker: str, limit_s: float) -> bool:
    """Read the subject's stdout until it prints the marker line."""
    assert proc.stdout is not None

    async def read() -> bool:
        while line := await proc.stdout.readline():  # type: ignore[union-attr]
            if line.decode(errors="replace").strip() == marker:
                return True
        return False

    try:
        return await asyncio.wait_for(read(), timeout=limit_s)
    except TimeoutError:
        return False


def shuffled_cells(
    factors: Sequence[Iterable[str]], trials_per_cell: int, seed: int
) -> list[tuple[str, ...]]:
    """Every combination of the factor levels, repeated, in a seeded random order.

    Running cells back to back in a fixed order confounds a cell with whatever
    drifts over a run. Shuffling spreads each cell's trials across the run; the
    seed is recorded so the order can be reproduced.
    """
    cells = [cell for cell in product(*factors) for _ in range(trials_per_cell)]
    random.Random(seed).shuffle(cells)
    return cells


def provenance(repo: Path, subject_python: str | None = None) -> dict:
    """What produced a results file: the commit, whether the tree was clean, the packages."""

    def run(*cmd: str) -> str:
        try:
            done = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    commit = run("git", "rev-parse", "HEAD")
    if commit:
        info: dict = {"git_commit": commit, "git_dirty": bool(run("git", "status", "--porcelain"))}
    else:
        # No git checkout, as inside a container: take what the caller passed in.
        dirty = os.environ.get("CRASHTEST_GIT_DIRTY", "")
        info = {
            "git_commit": os.environ.get("CRASHTEST_GIT_COMMIT") or None,
            "git_dirty": dirty.lower() in ("1", "true", "yes") if dirty else None,
        }
    if subject_python:
        frozen = run(subject_python, "-m", "pip", "freeze", "--disable-pip-version-check")
        info["subject_packages"] = sorted(frozen.splitlines())
    return info
