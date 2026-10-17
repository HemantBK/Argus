"""Print the corpus hash of an RBI library directory, the same value GET /health reports as corpus_hash.

    backend/.venv/bin/python scripts/corpus_hash.py [CORPUS_DIR]    (default: backend/data/corpus)

The library is loaded and checked exactly as the API loads it at startup (argus_api.corpus.store.Corpus), so a
directory the API would refuse is refused here too, and the hash is computed by the same function. Compare the
output with `curl <service>/health` to confirm which library a deployed API serves.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from argus_api.corpus.common import DEFAULT_OUT_DIR  # noqa: E402
from argus_api.corpus.store import Corpus  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print(f"usage: {Path(__file__).name} [CORPUS_DIR]", file=sys.stderr)
        return 2
    directory = Path(argv[0]) if argv else DEFAULT_OUT_DIR
    print(Corpus.load(directory).corpus_hash)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
