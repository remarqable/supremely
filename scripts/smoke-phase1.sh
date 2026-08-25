#!/bin/bash
# Phase 1 completion test against a live server (spec § Phase 1):
#   1. Run the installer.  2. Create Platform Admin.  3. Create an Organization.
#   4. Create a User.      5. Assign that User to the Organization.
#   6. Log in.             7. Switch Organizations.   8. Operate /admin.
set -e

PORT="${SMOKE_PORT:-8010}"
BASE="http://localhost:$PORT"
JAR=$(mktemp)
JAR2=$(mktemp)
GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

csrf() {  # csrf <jar> <url> -> token (also primes the session cookie)
  curl -s -c "$1" -b "$1" "$2" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1
}

# --- 0. Fresh installation ---------------------------------------------------
rm -rf data/smoke && mkdir -p data/smoke
export DATA_DIR=data/smoke PORT=$PORT APP_ENV=dev BASE_DOMAIN=localhost \
       SECRET_KEY=dev-secret-change-in-production USE_RELOADER=0
uv run flask db upgrade -q >/dev/null 2>&1 || uv run flask db upgrade >/dev/null
uv run python run.py >/tmp/supremely-smoke.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -f "$JAR" "$JAR2"' EXIT
for i in $(seq 1 30); do curl -s "$BASE/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -s "$BASE/health" | grep -q '"ok"' && pass "server up" || fail "server did not start"

# Uninstalled -> everything redirects to /setup
[ "$(curl -s -o /dev/null -w '%{redirect_url}' "$BASE/")" = "$BASE/setup" ] \
  && pass "uninstalled redirects to /setup" || fail "setup gate"

# --- 1+2+3. Installer: environment, database, Platform Admin, email, org ------
T=$(csrf "$JAR" "$BASE/setup/environment")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/setup/environment" \
  -d "csrf_token=$T&name=Smoke Install&base_url=http://localhost:$PORT&timezone=UTC&language=en" >/dev/null
T=$(csrf "$JAR" "$BASE/setup/admin")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/setup/admin" \
  -d "csrf_token=$T&password=smoke-secret-1&confirm_password=smoke-secret-1" >/dev/null
T=$(csrf "$JAR" "$BASE/setup/organization")
DONE=$(curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/setup/organization" \
  -d "csrf_token=$T&name=Acme Community&slug=acme")
echo "$DONE" | grep -q "Installation complete" && pass "installer completed" || fail "installer"

# Wizard now disabled
[ "$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/setup/")" = "404" ] \
  && pass "wizard disabled after install" || fail "wizard still open"

# Default-org mode: bare domain serves the single org
curl -s -b "$JAR" "$BASE/" | grep -q "Acme Community" && pass "default-org mode serves org" || fail "default-org"

# --- 8. Operate /admin as the wizard-created Platform Admin -------------------
curl -s -b "$JAR" "$BASE/admin/" | grep -q "Administration" && pass "/admin reachable" || fail "/admin"

# --- 4. Create a User via /admin ----------------------------------------------
T=$(csrf "$JAR" "$BASE/admin/users/new")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/admin/users/new" \
  -d "csrf_token=$T&email=member@smoke.test&name=Member&password=member-secret-1" >/dev/null
curl -s -b "$JAR" "$BASE/admin/users" | grep -q "member@smoke.test" && pass "user created" || fail "user create"

# --- 5. Assign that User to the Organization ----------------------------------
ORG_URL=$(curl -s -b "$JAR" "$BASE/admin/orgs" | sed -n 's/.*href="\(\/admin\/orgs\/[0-9]*\)".*/\1/p' | head -1)
T=$(csrf "$JAR" "$BASE$ORG_URL")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE$ORG_URL/members" \
  -d "csrf_token=$T&email=member@smoke.test&role=member" >/dev/null
curl -s -b "$JAR" "$BASE$ORG_URL" | grep -q "member@smoke.test" && pass "user assigned to org" || fail "assign"

# Second org (for switching): owned by the current admin (owner_id omitted)
T=$(csrf "$JAR" "$BASE/admin/orgs/new")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/admin/orgs/new" \
  -d "csrf_token=$T&name=Globex&slug=globex" >/dev/null
curl -s -b "$JAR" "$BASE/admin/orgs" | grep -q "Globex" && pass "second org created" || fail "second org"

# With two orgs the bare domain reverts to installation pages
curl -s "$BASE/" | grep -q "Acme Community" && fail "bare domain still org" || pass "bare domain reverted"
# Subdomain resolution
curl -s -H "Host: acme.localhost:$PORT" "$BASE/" | grep -q "Acme Community" \
  && pass "subdomain resolves org" || fail "subdomain"

# --- 6. Log in as the created member, on their org's subdomain ------------------
# (curl keys cookies on the Host header, so the member authenticates where
#  they browse — exactly what a real subdomain visitor does)
ACME_HOST="Host: acme.localhost:$PORT"
T=$(curl -s -c "$JAR2" -b "$JAR2" -H "$ACME_HOST" "$BASE/auth/login" \
    | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
curl -s -c "$JAR2" -b "$JAR2" -H "$ACME_HOST" -X POST "$BASE/auth/login" \
  -d "csrf_token=$T&email=member@smoke.test&password=member-secret-1" -o /dev/null -w '%{http_code}' | grep -q 302 \
  && pass "member logged in on org subdomain" || fail "member login"

# --- 7. Switch organizations: launcher lists both for the admin -----------------
LAUNCHER=$(curl -s -b "$JAR" "$BASE/launcher")
echo "$LAUNCHER" | grep -q "Acme Community" && echo "$LAUNCHER" | grep -q "Globex" \
  && pass "launcher lists both orgs (switcher)" || fail "launcher"

# Member's launcher redirects straight to their only org
[ "$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR2" -H "$ACME_HOST" "$BASE/launcher")" = "302" ] \
  && pass "single-org member redirected" || fail "member launcher"

# Member sees org dashboard, cannot see /admin
curl -s -b "$JAR2" -H "$ACME_HOST" "$BASE/dashboard" | grep -q "Acme Community" \
  && pass "member dashboard" || fail "member dashboard"
[ "$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR2" -H "$ACME_HOST" "$BASE/admin/")" = "404" ] \
  && pass "member cannot see /admin" || fail "admin leak"

echo ""
echo -e "${GREEN}Phase 1 completion test passed.${NC}"
