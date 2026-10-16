"""Copy a finished live run into the public, read-only samples/ collection as a labelled saved run.

Usage (from argus/backend, .env loaded):  make save-sample RUN=<run_id>
          or: .venv/bin/python -m argus_api.save_sample <run_id>

The copy keeps the run's own timestamps (so the app labels it "saved run <id> at <time it was recorded>"),
sets saved=true and holds the same steps, findings and audit log. Only a run with status done is copied, and
an existing sample is never overwritten.
"""

import argparse
import os

from google.cloud import firestore

from argus_api.docstore import FirestoreStore
from argus_api.runs import save_sample


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id")
    args = ap.parse_args(argv)
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set")
    run = save_sample(FirestoreStore(firestore.Client(project=project)), args.run_id)
    print(
        f"Saved run {run.run_id} (recorded {run.finished_at.isoformat() if run.finished_at else '?'}) "
        f"to samples/{run.run_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
