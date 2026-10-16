"""Operator commands against Firestore (never reachable from the browser).

  .venv/bin/python -m argus_api.admin load-corpus             write data/corpus to rbi_instruments,
                                                               rbi_paragraphs and supersession
  .venv/bin/python -m argus_api.admin limits --cap N --hourly-cap H --user-cap U --enabled true|false
                                                               set config/limits (daily, hourly and per-user
                                                               run caps, kill switch)
  .venv/bin/python -m argus_api.admin limits                  print config/limits

GOOGLE_CLOUD_PROJECT names the project; FIRESTORE_EMULATOR_HOST, when set, points at the emulator.
"""

import argparse
import os

from google.cloud import firestore

from argus_api.corpus.store import Corpus, load_to_firestore
from argus_api.docstore import FirestoreStore
from argus_api.runs import LIMITS_PATH, LimitsMissing, read_limits
from argus_api.schema import Limits


def _client() -> firestore.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set")
    return firestore.Client(project=project)


def _bool(value: str) -> bool:
    if value not in ("true", "false"):
        raise argparse.ArgumentTypeError("use true or false")
    return value == "true"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("load-corpus")
    lim = sub.add_parser("limits")
    lim.add_argument("--cap", type=int)
    lim.add_argument("--hourly-cap", type=int)
    lim.add_argument("--user-cap", type=int)
    lim.add_argument("--enabled", type=_bool)
    args = ap.parse_args(argv)

    client = _client()
    if args.command == "load-corpus":
        counts = load_to_firestore(client, Corpus.load())
        for collection, n in sorted(counts.items()):
            print(f"{collection}: {n} documents")
        return 0
    store = FirestoreStore(client)
    given = [args.cap, args.hourly_cap, args.user_cap, args.enabled]
    if any(v is None for v in given) and any(v is not None for v in given):
        ap.error("limits: give all of --cap, --hourly-cap, --user-cap and --enabled, or none to print")
    if args.cap is not None:
        limits = Limits(
            daily_run_cap=args.cap,
            hourly_run_cap=args.hourly_cap,
            per_user_daily_run_cap=args.user_cap,
            enabled=args.enabled,
        )
        store.set(LIMITS_PATH, limits.model_dump())
    try:
        limits = read_limits(store)
    except LimitsMissing as e:
        raise SystemExit(
            f"{e}. Set it with: make limits CAP=<runs per day> HOURLY_CAP=<per hour> "
            "USER_CAP=<per user per day> "
            "ENABLED=true"
        ) from e
    print(f"{LIMITS_PATH}: {limits.model_dump()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
