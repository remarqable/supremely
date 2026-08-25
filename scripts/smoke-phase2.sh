#!/bin/bash
# Phase 2 completion test: Supremely hosts a complete marketing website —
# pages, navigation, homepage designation, branding, theme — without touching
# core code. Everything is done through the web UI as an org owner.
set -e

PORT="${SMOKE_PORT:-8010}"
BASE="http://localhost:$PORT"
JAR=$(mktemp); ANON=$(mktemp)
GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
csrf() { curl -s -c "$1" -b "$1" "$2" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1; }

rm -rf data/smoke && mkdir -p data/smoke
export DATA_DIR=data/smoke PORT=$PORT APP_ENV=dev BASE_DOMAIN=localhost \
       SECRET_KEY=dev-secret-change-in-production USE_RELOADER=0
uv run flask db upgrade >/dev/null 2>&1
uv run python run.py >/tmp/supremely-smoke2.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -f "$JAR" "$ANON"' EXIT
for i in $(seq 1 30); do curl -s "$BASE/health" >/dev/null 2>&1 && break; sleep 0.5; done

# Install with one org; wizard leaves us logged in as the admin/owner.
T=$(csrf "$JAR" "$BASE/setup/environment")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/setup/environment" -d "csrf_token=$T&name=Smoke&base_url=$BASE&timezone=UTC&language=en" >/dev/null
T=$(csrf "$JAR" "$BASE/setup/database"); curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/setup/database" -d "csrf_token=$T&engine=sqlite" >/dev/null
T=$(csrf "$JAR" "$BASE/setup/admin"); curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/setup/admin" -d "csrf_token=$T&email=admin@smoke.test&password=smoke-secret-1&confirm_password=smoke-secret-1" >/dev/null
T=$(csrf "$JAR" "$BASE/setup/email"); curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/setup/email" -d "csrf_token=$T&skip=1" >/dev/null
T=$(csrf "$JAR" "$BASE/setup/organization")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/setup/organization" -d "csrf_token=$T&name=Ambient Labs&slug=ambient" | grep -q "Installation complete" \
  && pass "installed with org" || fail "install"

# Build the site: three pages, one members-only
T=$(csrf "$JAR" "$BASE/manage/content/page/new")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/manage/content/page/new" \
  --data-urlencode "csrf_token=$T" --data-urlencode "title=Welcome to Ambient Labs" \
  --data-urlencode "slug=welcome" --data-urlencode "visibility=public" --data-urlencode "action=publish" \
  --data-urlencode "body=# Software that breathes

Ambient Labs builds **calm technology**.

- Thoughtful tools
- No dark patterns" >/dev/null
T=$(csrf "$JAR" "$BASE/manage/content/page/new")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/manage/content/page/new" \
  --data-urlencode "csrf_token=$T" --data-urlencode "title=Our Story" --data-urlencode "slug=story" \
  --data-urlencode "visibility=public" --data-urlencode "action=publish" \
  --data-urlencode "seo_description=The story of Ambient Labs" \
  --data-urlencode "body=We started in a garage. The garage was nice." >/dev/null
T=$(csrf "$JAR" "$BASE/manage/content/page/new")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/manage/content/page/new" \
  --data-urlencode "csrf_token=$T" --data-urlencode "title=Team Handbook" --data-urlencode "slug=handbook" \
  --data-urlencode "visibility=members" --data-urlencode "action=publish" \
  --data-urlencode "body=Secret handshake instructions." >/dev/null
curl -s -b "$JAR" "$BASE/manage/content/page" | grep -q "Team Handbook" && pass "pages created" || fail "pages"

# The home page is the theme's editable hero — set it via Manage → Home page.
T=$(csrf "$JAR" "$BASE/manage/landing")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/manage/landing" \
  --data-urlencode "csrf_token=$T" \
  --data-urlencode "headline=Software that breathes" \
  --data-urlencode "subhead=Ambient Labs builds calm technology." \
  --data-urlencode "cta_label=Join us" --data-urlencode "cta_url=/subscribe" >/dev/null

# Navigation (primary nav is seeded; add a footer link of our own)
T=$(csrf "$JAR" "$BASE/manage/navigation")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/manage/navigation" -d "csrf_token=$T&menu=footer&label=Privacy&url=https://example.com/privacy" >/dev/null

# Branding
T=$(csrf "$JAR" "$BASE/manage/settings")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/manage/settings" \
  --data-urlencode "csrf_token=$T" --data-urlencode "section=branding" \
  --data-urlencode "name=Ambient Labs" --data-urlencode "description=Calm technology studio" \
  --data-urlencode "brand_primary=#0e7490" >/dev/null

# --- The completion test: an anonymous visitor sees a real website -----------
HOME=$(curl -s -c "$ANON" "$BASE/")
echo "$HOME" | grep -q "Software that breathes" && pass "home page hero renders" || fail "homepage"
echo "$HOME" | grep -q 'href="/blog"' && pass "primary navigation rendered (seeded)" || fail "nav"
echo "$HOME" | grep -q "example.com/privacy" && pass "footer navigation rendered" || fail "footer"
echo "$HOME" | grep -q "#0e7490" && pass "brand color applied" || fail "brand"

# Markdown renders on a content page
WELCOME=$(curl -s -b "$ANON" "$BASE/welcome")
echo "$WELCOME" | grep -q "<strong>calm technology</strong>" && pass "markdown rendered" || fail "markdown"

ABOUT=$(curl -s -b "$ANON" "$BASE/story")
echo "$ABOUT" | grep -q "The garage was nice" && pass "story page renders" || fail "story"
echo "$ABOUT" | grep -q 'name="description" content="The story of Ambient Labs"' && pass "SEO meta rendered" || fail "seo"

# Members-only page redirects anonymous to login; draft/unknown 404
[ "$(curl -s -o /dev/null -w '%{http_code}' -b "$ANON" "$BASE/handbook")" = "302" ] && pass "member page gated" || fail "gate"
[ "$(curl -s -o /dev/null -w '%{http_code}' -b "$ANON" "$BASE/nope")" = "404" ] && pass "unknown page 404" || fail "404"

# Owner (logged in) sees the members-only page
curl -s -b "$JAR" "$BASE/handbook" | grep -q "Secret handshake" && pass "member sees gated page" || fail "member gate"

# Theme switch: midnight, with an accent setting
T=$(csrf "$JAR" "$BASE/manage/settings")
curl -s -c "$JAR" -b "$JAR" -X POST "$BASE/manage/settings" \
  --data-urlencode "csrf_token=$T" --data-urlencode "section=theme" \
  --data-urlencode "theme=midnight" --data-urlencode "theme_accent=#f59e0b" >/dev/null
THEMED=$(curl -s "$BASE/")
echo "$THEMED" | grep -q "bg-slate-950" && pass "midnight theme layout active" || fail "theme layout"
echo "$THEMED" | grep -q "#f59e0b" && pass "theme setting applied" || fail "theme setting"
# Pages are theme-independent content and still render after a theme switch
curl -s "$BASE/story" | grep -q "The garage was nice" && pass "content survives theme switch" || fail "content/theme"
curl -s "$BASE/themes/midnight/static/theme.css" | grep -q "midnight-accent" && pass "theme asset served" || fail "asset"

echo ""
echo -e "${GREEN}Phase 2 completion test passed: a complete website, no core changes.${NC}"
