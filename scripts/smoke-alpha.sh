#!/bin/bash
# Phase 9 public alpha test (spec):
#   git clone / docker compose up   -> here: fresh data dir + dev server
#   complete wizard -> create organization -> select theme -> create pages
#   -> publish post -> invite members -> start discussions
# All over HTTP, with no knowledge of Supremely internals.
set -e

PORT="${SMOKE_PORT:-8010}"
BASE="http://localhost:$PORT"
OWNER=$(mktemp); FRIEND=$(mktemp)
GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
csrf() { curl -s -c "$1" -b "$1" "$2" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1; }
post() { local jar=$1 url=$2; shift 2; local T=$(csrf "$jar" "${4:-$url}"); curl -s -c "$jar" -b "$jar" -X POST "$url" --data-urlencode "csrf_token=$T" "$@"; }

rm -rf data/smoke && mkdir -p data/smoke
export DATA_DIR=data/smoke PORT=$PORT APP_ENV=dev BASE_DOMAIN=localhost \
       SECRET_KEY=dev-secret-change-in-production USE_RELOADER=0
uv run flask db upgrade >/dev/null 2>&1
uv run python run.py >/tmp/supremely-alpha.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -f "$OWNER" "$FRIEND"' EXIT
for i in $(seq 1 30); do curl -s "$BASE/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -s "$BASE/health" | grep -q '"ok"' && pass "server up (docker compose up equivalent)" || fail "boot"

# --- complete wizard -----------------------------------------------------------
T=$(csrf "$OWNER" "$BASE/setup/environment")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/setup/environment" -d "csrf_token=$T&name=Alpha Install&base_url=$BASE&timezone=UTC&language=en" >/dev/null
T=$(csrf "$OWNER" "$BASE/setup/admin"); curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/setup/admin" -d "csrf_token=$T&password=alpha-secret-99&confirm_password=alpha-secret-99" >/dev/null
T=$(csrf "$OWNER" "$BASE/setup/organization")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/setup/organization" -d "csrf_token=$T&skip=1" | grep -q "Installation complete" \
  && pass "wizard completed" || fail "wizard"

# --- create organization ----------------------------------------------------------
T=$(csrf "$OWNER" "$BASE/launcher/new")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/launcher/new" \
  --data-urlencode "csrf_token=$T" --data-urlencode "name=Night Owls" --data-urlencode "slug=night-owls" -o /dev/null
curl -s -b "$OWNER" "$BASE/" | grep -q "Night Owls" && pass "organization created" || fail "org"

# --- select theme ------------------------------------------------------------------
T=$(csrf "$OWNER" "$BASE/manage/settings")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/manage/settings" \
  --data-urlencode "csrf_token=$T" --data-urlencode "section=theme" \
  --data-urlencode "theme=midnight" --data-urlencode "theme_accent=#38bdf8" >/dev/null
curl -s "$BASE/" | grep -q "bg-slate-950" && pass "theme selected (midnight)" || fail "theme"

# --- create pages --------------------------------------------------------------------
T=$(csrf "$OWNER" "$BASE/manage/content/page/new")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/manage/content/page/new" \
  --data-urlencode "csrf_token=$T" --data-urlencode "title=Hello Night" \
  --data-urlencode "slug=hello" --data-urlencode "visibility=public" \
  --data-urlencode "action=publish" --data-urlencode "body=# We meet after dark" >/dev/null
curl -s "$BASE/hello" | grep -q "We meet after dark" && pass "page created and public" || fail "page"

# --- publish post ---------------------------------------------------------------------
T=$(csrf "$OWNER" "$BASE/manage/content/article/new")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/manage/content/article/new" \
  --data-urlencode "csrf_token=$T" --data-urlencode "title=First Flight" \
  --data-urlencode "slug=first-flight" --data-urlencode "visibility=public" \
  --data-urlencode "action=publish" --data-urlencode "body=Owls, assemble." >/dev/null
curl -s "$BASE/blog/first-flight" | grep -q "Owls, assemble." && pass "post published" || fail "post"

# --- invite members (no email service anywhere) ------------------------------------------
T=$(csrf "$OWNER" "$BASE/manage/members")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/manage/invitations" -d "csrf_token=$T&role=member" -o /dev/null
INVITE=$(curl -s -c "$OWNER" -b "$OWNER" "$BASE/manage/members")
URL=$(echo "$INVITE" | sed -n 's/.*value="\(http[^"]*\/invite\/[^"]*\)".*/\1/p' | head -1)
[ -n "$URL" ] && pass "invitation link generated" || fail "invite"
TOKEN=${URL##*/}
T=$(csrf "$FRIEND" "$BASE/invite/$TOKEN")
curl -s -c "$FRIEND" -b "$FRIEND" -X POST "$BASE/invite/$TOKEN/signup" \
  --data-urlencode "csrf_token=$T" --data-urlencode "name=Friend" \
  --data-urlencode "email=friend@test.dev" --data-urlencode "password=friend-secret-9" -o /dev/null
curl -s -b "$FRIEND" "$BASE/dashboard" | grep -q "Night Owls" && pass "member joined via link" || fail "join"

# --- start discussions ---------------------------------------------------------------------
T=$(csrf "$OWNER" "$BASE/manage/discussions")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/manage/discussions" \
  -d "csrf_token=$T&name=Lounge&slug=lounge&visibility=members" >/dev/null
T=$(csrf "$OWNER" "$BASE/discussions/lounge")
curl -s -c "$OWNER" -b "$OWNER" -X POST "$BASE/discussions/lounge/new" \
  --data-urlencode "csrf_token=$T" --data-urlencode "title=Introductions" \
  --data-urlencode "body=Say hello!" >/dev/null
T=$(csrf "$FRIEND" "$BASE/discussions/lounge/1")
curl -s -c "$FRIEND" -b "$FRIEND" -X POST "$BASE/discussions/lounge/1/comment" \
  --data-urlencode "csrf_token=$T" --data-urlencode "body=Hello from the friend!" >/dev/null
curl -s -b "$OWNER" "$BASE/discussions/lounge/1" | grep -q "Hello from the friend!" \
  && pass "discussion started, member replied" || fail "discussion"

# Owner got notified of the reply
curl -s -b "$OWNER" "$BASE/notifications/" | grep -q "Introductions" \
  && pass "comment notification received" || fail "notification"

# --- dogfood: supremely.org seeded on the same install ----------------------------------
uv run flask seed supremely >/dev/null 2>&1 && pass "supremely org seeded (dogfood)" || fail "seed"
# supremely.org runs on the Supremely marketing theme (dogfood)
curl -s -H "Host: supremely.localhost:$PORT" "$BASE/" | grep -q "The open-source" \
  && pass "supremely.org homepage serves" || fail "dogfood home"
curl -s -H "Host: supremely.localhost:$PORT" "$BASE/docs" | grep -q "docker compose up" \
  && pass "docs page serves" || fail "docs"
curl -s -H "Host: supremely.localhost:$PORT" "$BASE/blog" | grep -q "Supremely now runs on Supremely" \
  && pass "dogfood post listed" || fail "dogfood post"
curl -s -H "Host: supremely.localhost:$PORT" "$BASE/subscribe" | grep -qi "subscribe" \
  && pass "newsletter signup page serves" || fail "subscribe"
curl -s -H "Host: supremely.localhost:$PORT" "$BASE/discussions/" | grep -q "Development" \
  && pass "community discussions visible" || fail "dogfood discussions"

echo ""
echo -e "${GREEN}Public alpha test passed: wizard → org → theme → pages → post → invite → discuss, plus dogfood.${NC}"
