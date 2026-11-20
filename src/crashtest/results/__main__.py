"""Result store command line.

    python -m crashtest.results load  --database-url URL [FILES...]   (default: results/*/*.json)
    python -m crashtest.results cells --database-url URL EXPERIMENT

With --local instead of --database-url, the store lives in the local pgserver
database used by the experiments.
"""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg

from crashtest.results.store import FACTORS, cell_counts, load_files

REPO = Path(__file__).resolve().parents[3]


@contextmanager
def _database(args: argparse.Namespace):
    if args.local:
        import pgserver

        pg = pgserver.get_server(str((REPO / ".pgdata").resolve()), cleanup_mode="stop")
        try:
            yield pg.get_uri()
        finally:
            pg.cleanup()
    else:
        yield args.database_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--database-url")
    where.add_argument("--local", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load")
    load.add_argument("files", nargs="*", type=Path)
    cells = sub.add_parser("cells")
    cells.add_argument("experiment", choices=sorted(FACTORS))
    args = parser.parse_args()

    with _database(args) as url, psycopg.connect(url, autocommit=True) as conn:
        if args.command == "load":
            files = args.files or sorted(REPO.glob("results/*/*.json"))
            for path, status, n in load_files(conn, files, REPO):
                shown = path.relative_to(REPO) if path.is_absolute() else path
                print(f"{status:15} {n:5} trials  {shown}")
        else:
            rows = cell_counts(conn, args.experiment)
            if not rows:
                print("nothing loaded for", args.experiment)
                return 1
            cols = list(rows[0])
            print("| " + " | ".join(cols) + " |")
            print("|" + "---|" * len(cols))
            for r in rows:
                print("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
