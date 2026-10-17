"""Operator commands against Firestore (never reachable from the browser).

  .venv/bin/python -m argus_api.admin load-corpus             write data/corpus to rbi_instruments,
                                                               rbi_paragraphs and supersession
  .venv/bin/python -m argus_api.admin limits --cap N --hourly-cap H --user-cap U --enabled true|false
                                                               set config/limits (daily, hourly and per-user
                                                               run caps, kill switch)
  .venv/bin/python -m argus_api.admin limits                  print config/limits
  .venv/bin/python -m argus_api.admin custom --enabled true --cap N
                                                               switch pasted-clause runs on with a daily cap
                                                               (config/custom_limits)
  .venv/bin/python -m argus_api.admin custom --enabled false  switch pasted-clause runs off, keeping the cap
  .venv/bin/python -m argus_api.admin custom                  print config/custom_limits (missing: off)
  .venv/bin/python -m argus_api.admin ttl                     print (never run) the gcloud commands that turn
                                                               on Firestore TTL on expires_at for the runs,
                                                               steps, findings and audit collection groups

Pasted-clause runs are switched in their own document, config/custom_limits, never in config/limits: every
backend revision validates config/limits strictly and refuses all runs when it holds a field the revision does
not know. Revisions that predate pasted clauses never read config/custom_limits, so `custom` is safe to run
before a deploy and after a rollback, and `custom --enabled false` switches the feature off whatever is
serving. Expired pasted-clause runs are deleted by Firestore TTL (`ttl`) and at once by
`python -m argus_api.purge_custom`.

GOOGLE_CLOUD_PROJECT names the project; FIRESTORE_EMULATOR_HOST, when set, points at the emulator.
"""

import argparse
import os

from google.cloud import firestore

from argus_api.corpus.store import Corpus, load_to_firestore
from argus_api.docstore import DocumentStore, FirestoreStore
from argus_api.runs import (
    CUSTOM_LIMITS_PATH,
    EXPIRES_AT,
    LIMITS_PATH,
    RUN_CHILDREN,
    RUNS,
    LimitsMissing,
    read_custom_limits,
    read_limits,
)
from argus_api.schema import CustomLimits, Limits

# Every collection group a custom run writes; each document in them carries expires_at (runs.child_doc).
TTL_COLLECTION_GROUPS = (RUNS, *RUN_CHILDREN)


def ttl_commands(project: str) -> list[str]:
    """The gcloud commands that enable TTL on expires_at for every TTL_COLLECTION_GROUPS group, then list the
    policies to confirm them. A TTL policy deletes a document typically within 24 hours after expires_at."""
    enable = [
        f"gcloud firestore fields ttls update {EXPIRES_AT} --collection-group={group} --enable-ttl "
        f"--database='(default)' --project={project} --async"
        for group in TTL_COLLECTION_GROUPS
    ]
    return [*enable, f"gcloud firestore fields ttls list --database='(default)' --project={project}"]


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
    cus = sub.add_parser("custom")
    cus.add_argument("--enabled", type=_bool)
    cus.add_argument("--cap", type=int)
    sub.add_parser("ttl")
    args = ap.parse_args(argv)

    if args.command == "ttl":
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip() or "<project>"
        print("# Run these once per project (not run by this command):")
        for line in ttl_commands(project):
            print(line)
        return 0
    client = _client()
    if args.command == "load-corpus":
        counts = load_to_firestore(client, Corpus.load())
        for collection, n in sorted(counts.items()):
            print(f"{collection}: {n} documents")
        return 0
    store = FirestoreStore(client)
    if args.command == "custom":
        return _custom(store, args.enabled, args.cap, ap)
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


def _custom(store: DocumentStore, enabled: bool | None, cap: int | None, ap: argparse.ArgumentParser) -> int:
    """Set or print config/custom_limits. Switching on needs a cap; switching off keeps the stored cap."""
    if cap is not None and enabled is None:
        ap.error("custom: give --enabled with --cap")
    if enabled is not None:
        current = read_custom_limits(store)
        if cap is None and enabled:
            ap.error("custom: switching custom runs on needs --cap")
        new_cap = cap if cap is not None else (current.custom_daily_cap if current is not None else 0)
        store.set(
            CUSTOM_LIMITS_PATH, CustomLimits(custom_enabled=enabled, custom_daily_cap=new_cap).model_dump()
        )
    shown = read_custom_limits(store)
    if shown is None:
        state = "missing" if store.get(CUSTOM_LIMITS_PATH) is None else "malformed"
        print(f"{CUSTOM_LIMITS_PATH}: {state}, so custom runs are off")
    else:
        print(f"{CUSTOM_LIMITS_PATH}: {shown.model_dump()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
