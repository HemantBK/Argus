"""Copy a finished live run into the public, read-only samples/ collection as a labelled saved run.

Usage (from argus/backend, .env loaded):  make save-sample RUN=<run_id>
          or: .venv/bin/python -m argus_api.save_sample <run_id>

The copy keeps the run's own timestamps (so the app labels it "saved run <id> at <time it was recorded>"),
sets saved=true and holds the same steps, findings and audit log. An existing sample is never overwritten.
Only a real review is copied (runs.sample_refusal): status done, provider vertex, not seeded, the pinned
config.PINNED_MODELS["vertex"] model ids, and an rbi_as_on date. There is no flag to skip these checks.
"""

import argparse
import os

from google.cloud import firestore

from argus_api.docstore import FirestoreStore
from argus_api.runs import Rejected, save_sample


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id")
    args = ap.parse_args(argv)
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set")
    try:
        run = save_sample(FirestoreStore(firestore.Client(project=project)), args.run_id)
    except Rejected as e:
        raise SystemExit(f"save-sample refused: {e}") from e
    print(
        f"Saved run {run.run_id} (recorded {run.finished_at.isoformat() if run.finished_at else '?'}) "
        f"to samples/{run.run_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
