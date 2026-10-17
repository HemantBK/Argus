#!/usr/bin/env bash
# firebase.json hosting.predeploy: refuse a web deploy that did not come through scripts/release.sh web.
# On 18 Oct a hand-run `firebase deploy` put a stale build live: the paste card showed open while the API's
# switch was off. release.sh web builds from a clean tree, checks the build stamp against the API and its
# settings, and sets ARGUS_RELEASE=web for the one deploy it runs.
set -euo pipefail
if [[ "${ARGUS_RELEASE:-}" != "web" ]]; then
  echo "Refusing to deploy Hosting by hand. Release the web with: scripts/release.sh web" >&2
  echo "(it checks web/dist/version.json against the API before anything goes live)" >&2
  exit 1
fi
[[ -f web/dist/version.json ]] || { echo "web/dist/version.json is missing: build with scripts/release.sh web" >&2; exit 1; }
