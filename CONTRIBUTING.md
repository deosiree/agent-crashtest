# Contributing

Thanks for your interest. The project is early, so the most useful contributions are bug reports, reproductions and new fault scenarios.

## Set up

[README.md](README.md) covers setup on Windows, on Linux or macOS, and with Docker. In short, on Linux or macOS:

```bash
python3.12 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

## Before opening a pull request

All four must pass; CI runs the same checks.

```bash
ruff check src tests
```

```bash
ruff format --check src tests
```

```bash
mypy
```

```bash
pytest
```

## Rules for results

- A result must be reproducible from a results file in `results/` and a command in its experiment write-up.
- Write-ups state what was run, the versions, and what the result does **not** show.
- Do not discard runs that look wrong. Keep them and explain them, as experiment 001 does.
- Before publishing a result about another project, share the reproduction with its maintainers first.

## Scope

Faults belong in the sink or the injector, not in the subject under test. A subject should run unmodified, so the result reflects the subject and not the harness.
