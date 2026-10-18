#!/usr/bin/env bash
# Release the current commit to production in approved steps, stopping at the first failed check.
#
#   scripts/release.sh candidate   tests, deploy a new Cloud Run revision with NO traffic (tag "candidate"),
#                                  check its /health and run one live review of sample policy A on it
#   scripts/release.sh promote     move all traffic to that revision, re-check on the public URL (traffic
#                                  goes back to the previous revision if this fails), min instances 1,
#                                  switch on Firestore TTL for pasted-clause runs, then release the web app
#                                  exactly as `web` does
#   scripts/release.sh web         release only the web app and security rules, for a commit whose API code
#                                  equals the serving API's: clean tree, build, check the build stamp (and,
#                                  when it offers pasting a clause, that config/custom_limits is on) before
#                                  deploying, then check the hosted build stamp (check.py D-WEB-SHA) and the
#                                  rules with real tokens
#
# Every web release goes through deploy_web, so no web build reaches Hosting without the stamp check. A person
# reads the candidate result and runs promote; nothing moves traffic on its own. Run from the repo root with
# gcloud and firebase signed in. Evidence goes to ../verification/<today>/release/. The pasted-clause switch is
# not touched here: `backend/.venv/bin/python -m argus_api.admin custom --enabled true|false --cap N`.
set -euo pipefail

PROJECT=argus-aibc
REGION=asia-south1
SERVICE=argus-api
SITE=argus-aibc
PUBLIC_API="https://argus-api-419264532549.asia-south1.run.app"
HOSTING_LIVE_API="https://firebasehosting.googleapis.com/v1beta1/sites/$SITE/channels/live"
# What the API runs; the eval harness is not served. check.py web_api_compatibility uses the same paths.
API_PATHS=(backend/argus_api ':!backend/argus_api/eval')
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

# The Hosting version now on the live channel, so a bad web release can be put back with hosting:clone.
live_hosting_version() {
  curl -fsS -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "x-goog-user-project: $PROJECT" \
    "$HOSTING_LIVE_API" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["release"]["version"]["name"].rsplit("/",1)[-1])'
}

# One production Firestore document as REST JSON (read only). Fails when it is missing or cannot be read.
firestore_doc() {
  curl -fsS --max-time 60 -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "x-goog-user-project: $PROJECT" \
    "https://firestore.googleapis.com/v1/projects/$PROJECT/databases/(default)/documents/$1"
}

# Build HEAD, deploy it to Hosting with the security rules, and check what is live. Extra arguments go to
# check.py deploy (promote passes --expect-sha). On a failed check, print the rollback and stop.
deploy_web() {
  local head api_sha prev rollback custom_open custom_doc
  step "Web pre-checks"
  [[ -z "$(git status --porcelain)" ]] || fail "working tree is not clean: the build stamp would not name what was built"
  head=$(git rev-parse HEAD)
  api_sha=$(curl -fsS --max-time 60 "$PUBLIC_API/health" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["git_sha"])') || fail "could not read $PUBLIC_API/health"
  if [[ "$api_sha" != "$head" ]]; then
    git merge-base --is-ancestor "$api_sha" "$head" ||
      fail "HEAD ${head:0:12} does not descend from the serving API ${api_sha:0:12}"
    [[ -z "$(git diff --name-only "$api_sha..$head" -- "${API_PATHS[@]}")" ]] ||
      fail "HEAD changes the API code since the serving API ${api_sha:0:12}: release it with candidate, then promote"
  fi
  prev=$(live_hosting_version) || fail "could not read the live Hosting version, so a rollback would not be possible"
  rollback="firebase hosting:clone $SITE@$prev $SITE:live --project $PROJECT"
  printf 'commit %s; serving API %s; live Hosting version %s\n' "$head" "$api_sha" "$prev"
  printf 'PREV_HOSTING_VERSION=%s\nWEB_SHA=%s\nROLLBACK=%q\n' "$prev" "$head" "$rollback" > "$EVIDENCE/web_state.env"

  step "Build the web app"
  (cd web && npm run build >/dev/null) || fail "web build failed"
  # A build that offers "Try your own clause" must not reach Hosting while the API's switch is off: on 18 Oct
  # such a build was live and every pasted clause failed. Read config/custom_limits only when the build offers
  # pasting; a missing or unreadable document stops the release, since the API then refuses every paste.
  custom_open=$(python3 -c 'import json; print(json.load(open("web/dist/version.json")).get("customClauseOpen"))') ||
    fail "web/dist/version.json is missing or not JSON"
  custom_doc="$EVIDENCE/custom_limits.json"
  : > "$custom_doc"
  if [[ "$custom_open" == "True" ]]; then
    firestore_doc config/custom_limits > "$custom_doc" ||
      fail "the web build offers 'Try your own clause' but config/custom_limits is missing or could not be read, so the API would refuse every pasted clause"
  fi
  python3 - "$head" "$PUBLIC_API" "$custom_doc" <<'CHECK_STAMP' || fail "web/dist/version.json does not describe a clean build of HEAD for the production API and its settings"
import json, sys
stamp = json.load(open("web/dist/version.json"))
head, api, custom_path = sys.argv[1], sys.argv[2], sys.argv[3]
custom_open = stamp.get("customClauseOpen")
enabled = None
if custom_open is True:
    fields = json.load(open(custom_path)).get("fields", {})
    enabled = fields.get("custom_enabled", {}).get("booleanValue")
problems = [p for p, bad in (
    (f"sha {stamp.get('sha')} is not HEAD {head}", stamp.get("sha") != head),
    ("dirty is not false", stamp.get("dirty") is not False),
    (f"apiBase {stamp.get('apiBase')} is not {api}", stamp.get("apiBase") != api),
    (f"customClauseOpen is {custom_open!r}, not true or false", not isinstance(custom_open, bool)),
    (
        f"customClauseOpen is true but config/custom_limits custom_enabled is {enabled!r}: "
        "the API would refuse every pasted clause",
        custom_open is True and enabled is not True,
    ),
) if bad]
print("\n".join(problems) or f"web/dist/version.json: {json.dumps(stamp)}")
sys.exit(1 if problems else 0)
CHECK_STAMP

  step "Deploy the web app and security rules"
  ARGUS_RELEASE=web firebase deploy --only hosting,firestore:rules --project "$PROJECT" ||
    fail "firebase deploy failed. If Hosting changed, roll it back: $rollback"

  step "Deployment checks (including D-WEB-SHA: the hosted build stamp)"
  if ! $PY scripts/check.py deploy --strict --expect-web-sha HEAD --evidence-dir "$EVIDENCE" "$@"; then
    fail "check.py deploy reported a failure. Roll the web app back to the version that was live before:
  $rollback
(or Firebase console > Hosting > Release history > Roll back, on version $prev)"
  fi
  $PY scripts/cloud_e2e.py --out "$EVIDENCE" --gate-id WEB-RULES rules ||
    fail "security rules check failed. Redeploy the rules from the last good commit; the web rollback is: $rollback"
  echo
  echo "Web $head is live on Hosting. Rollback: $rollback"
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
  # HEAD may have moved past the candidate only by commits that leave the API code alone (docs, web, scripts):
  # the web deploy below builds HEAD, and the API revision being promoted is still exactly the candidate.
  if [[ "$(git rev-parse HEAD)" != "$SHA" ]]; then
    git merge-base --is-ancestor "$SHA" HEAD || fail "HEAD does not descend from the candidate ${SHA:0:12}"
    [[ -z "$(git diff --name-only "$SHA..HEAD" -- "${API_PATHS[@]}")" ]] ||
      fail "HEAD changes the API code since the candidate ${SHA:0:12}: build a new candidate"
  fi

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

  step "Firestore TTL on expires_at (pasted-clause runs only carry it)"
  for group in runs steps findings audit; do
    gcloud firestore fields ttls update expires_at --collection-group="$group" --enable-ttl \
      --database='(default)' --project="$PROJECT" --async --quiet
  done
  gcloud firestore fields ttls list --database='(default)' --project="$PROJECT"

  echo "API rollback, if the web release below stops: gcloud run services update-traffic $SERVICE" \
    "--region $REGION --project $PROJECT --to-revisions $OLD=100"
  deploy_web --expect-sha "$SHA"
  echo
  echo "Released $SHA as $NEW. Rollback: gcloud run services update-traffic $SERVICE --region $REGION --project $PROJECT --to-revisions $OLD=100"
}

case "${1:-}" in
  candidate) candidate ;;
  promote) promote ;;
  web) deploy_web ;;
  *) fail "usage: scripts/release.sh candidate|promote|web" ;;
esac
