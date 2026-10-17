#!/usr/bin/env bash
# Release the current commit to production in two approved steps, stopping at the first failed check.
#
#   scripts/release.sh candidate   tests, deploy a new Cloud Run revision with NO traffic (tag "candidate"),
#                                  check its /health and run one live review of sample policy A on it
#   scripts/release.sh promote     move all traffic to that revision, re-check on the public URL (traffic
#                                  goes back to the previous revision if this fails), min instances 1,
#                                  deploy the web app and security rules, check the rules with real tokens,
#                                  switch on Firestore TTL for pasted-clause runs
#
# A person reads the candidate result and runs promote; nothing moves traffic on its own. Run from the repo
# root with gcloud and firebase signed in. Evidence goes to ../verification/<today>/release/. The pasted-clause
# switch is not touched here: `backend/.venv/bin/python -m argus_api.admin custom --enabled true|false --cap N`.
set -euo pipefail

PROJECT=argus-aibc
REGION=asia-south1
SERVICE=argus-api
PUBLIC_API="https://argus-api-419264532549.asia-south1.run.app"
PY=backend/.venv/bin/python
EVIDENCE="../verification/$(date +%Y-%m-%d)/release"
STATE="$EVIDENCE/release_state.env"

step() { printf '\n== %s ==\n' "$*"; }
fail() { printf '\nRELEASE STOPPED: %s\n' "$*" >&2; exit 1; }

[[ -f scripts/release.sh ]] || fail "run from the repo root"
mkdir -p "$EVIDENCE"

serving_revision() {
  gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format=json |
    python3 -c 'import json,sys; t=json.load(sys.stdin)["status"]["traffic"]; print(next(x["revisionName"] for x in t if x.get("percent")==100))'
}

candidate() {
  step "Pre-checks"
  [[ -z "$(git status --porcelain)" ]] || fail "working tree is not clean"
  SHA=$(git rev-parse HEAD)
  make test lint >/dev/null || fail "backend tests or lint failed"
  (cd web && npm run build >/dev/null) || fail "web build failed"
  OLD=$(serving_revision)
  echo "commit $SHA; serving now $OLD"

  step "Deploy a candidate revision with no traffic"
  (cd backend && gcloud run deploy "$SERVICE" --source . --region "$REGION" --project "$PROJECT" \
    --service-account "$SERVICE@$PROJECT.iam.gserviceaccount.com" \
    --no-traffic --tag candidate --update-env-vars "ARGUS_GIT_SHA=$SHA" --quiet) || fail "deploy failed"
  NEW=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
    --format='value(status.latestCreatedRevisionName)')
  URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format=json |
    python3 -c 'import json,sys; t=json.load(sys.stdin)["status"]["traffic"]; print(next(x["url"] for x in t if x.get("tag")=="candidate"))')
  printf 'SHA=%s\nOLD=%s\nNEW=%s\nCANDIDATE_URL=%s\n' "$SHA" "$OLD" "$NEW" "$URL" > "$STATE"
  echo "candidate $NEW at $URL (no traffic)"

  step "Check the candidate: health, then one live review of policy A"
  $PY scripts/cloud_e2e.py --api "$URL" --out "$EVIDENCE" --gate-id CANDIDATE-SMOKE smoke ||
    fail "candidate smoke failed; it has no traffic, the live app is unchanged ($OLD)"
  echo
  echo "Candidate $NEW passed. The live app still serves $OLD. Next: scripts/release.sh promote"
}

promote() {
  [[ -f "$STATE" ]] || fail "no candidate recorded; run scripts/release.sh candidate first"
  # shellcheck disable=SC1090
  source "$STATE"
  [[ "$(git rev-parse HEAD)" == "$SHA" ]] || fail "HEAD moved since the candidate was built"

  step "Move all traffic to $NEW"
  gcloud run services update-traffic "$SERVICE" --region "$REGION" --project "$PROJECT" \
    --to-revisions "$NEW=100" --quiet || fail "traffic switch failed"

  step "Check the public URL"
  if ! $PY scripts/cloud_e2e.py --api "$PUBLIC_API" --out "$EVIDENCE" --gate-id PROMOTED-SMOKE smoke; then
    gcloud run services update-traffic "$SERVICE" --region "$REGION" --project "$PROJECT" \
      --to-revisions "$OLD=100" --quiet
    fail "public smoke failed; traffic is back on $OLD"
  fi

  step "Keep one instance warm for judging"
  gcloud run services update "$SERVICE" --region "$REGION" --project "$PROJECT" --min-instances 1 --quiet

  step "Deploy the web app and security rules"
  (cd web && npm run build >/dev/null) || fail "web build failed"
  firebase deploy --only hosting,firestore:rules --project "$PROJECT" || fail "firebase deploy failed"
  $PY scripts/cloud_e2e.py --out "$EVIDENCE" --gate-id PROMOTED-RULES rules || fail "security rules check failed"

  step "Firestore TTL on expires_at (pasted-clause runs only carry it)"
  for group in runs steps findings audit; do
    gcloud firestore fields ttls update expires_at --collection-group="$group" --enable-ttl \
      --database='(default)' --project="$PROJECT" --async --quiet
  done
  gcloud firestore fields ttls list --database='(default)' --project="$PROJECT"

  step "Deployment checks"
  $PY scripts/check.py deploy --strict --expect-sha "$SHA" --evidence-dir "$EVIDENCE" ||
    fail "check.py deploy reported a failure"
  echo
  echo "Released $SHA as $NEW. Rollback: gcloud run services update-traffic $SERVICE --region $REGION --project $PROJECT --to-revisions $OLD=100"
}

case "${1:-}" in
  candidate) candidate ;;
  promote) promote ;;
  *) fail "usage: scripts/release.sh candidate|promote" ;;
esac
