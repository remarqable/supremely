# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Supremely is an open-source, multi-tenant Flask platform for publishing, memberships, newsletters, and community. The tenant is the **Organization**. One codebase serves self-hosted installs, third-party SaaS providers, and the hosted service. Email is optional infrastructure everywhere — installing, publishing, and onboarding must never require it.

## Commands

```bash
make install      # uv sync
make css          # build Tailwind (standalone binary, downloaded once to bin/)
make run          # sync dev schema from models, then dev server on :8000
make worker       # background job worker (flask jobs run)
make test         # uv run pytest
make db           # flask dev sync-db — dev schema straight from models
make migrate      # flask db upgrade — prod/CI path only
make reset        # wipe data/ (DB, wizard config, uploads); re-runs setup wizard
```

- Single test: `uv run pytest tests/test_platform/test_content_types.py -k test_name`
- Tests run on in-memory SQLite by default; set `TEST_DATABASE_URL=postgresql://...` to run the same suite on PostgreSQL (CI runs both).
- Everything goes through `uv run` — there is no requirements.txt and no venv activation.
- Useful CLI: `flask users reset-password EMAIL`, `flask users create-admin EMAIL`, `flask jobs work-off` (drain queue once), `flask setup reset`, `flask seed getsupremely` (dogfood site).

## The blueprint is authoritative

`blueprint/` is the engineering pattern library (its own `CLAUDE.md` + `patterns/`). Before adding a feature, find the relevant pattern doc and follow it — do not invent a second way to do something the blueprint covers. Supremely's configuration: `tenancy: shared`, `plugins/theming/uploads/jobs: true`, `auth: password`, `database: sqlite` (PostgreSQL via `DATABASE_URL`), `deploy: docker`. Read layer docs only for enabled layers.

Non-negotiables from the blueprint:
- `BigIntPK` for primary keys; `utcnow()` never `datetime.utcnow()`.
- Business models inherit `OrgScoped`; queries are **never** manually filtered by `org_id` — the tenant filter in `app/platform/tenant.py` does it globally.
- Run `make css` after touching any template and commit the built `app/static/css/app.css` — an uncompiled Tailwind class renders unstyled only in production.
- Logical properties (`ms-`/`me-`/`ps-`/`pe-`/`text-start`), never `ml-`/`mr-`/`text-left` (RTL).
- Migrations only via the deploy path, never inside `create_app`. Dev uses `flask dev sync-db` (schema from models); author Alembic migrations only for prod/CI.
- Plugin templates use `plugin_url_for`, never `url_for`.

## UI architecture: one surface, two modes, plus a console

Authority: `../supremely-dev/Supremely UI Architecture Direction — One Surface, Two Modes, Plus a Console.md`. The community is the application: members and admins share the same routes and layouts. Before building management UI, ask two questions — *acting on the object in front of you?* → inline on the community surface (visible per `can()`, surfaced prominently by manage mode); *rules, queues, bulk ops, or configuration?* → the console (`/manage` for orgs, `/admin` for the platform). Never build a parallel `/manage` CRUD for content that can be managed where it lives.

- **Manage mode** (`session['manage_mode']`, `managing()` template helper) is presentation state, never authorization — every control is individually permission-gated and the backend enforces each action regardless.
- **Themes style the public site only.** The authenticated community shell (`app/views/layouts/community.html` + `app/views/community/*`) is app-owned and never theme-resolvable; themes may tint it only through the approved token whitelist (`theming.community_tokens`). `render_site` picks community templates and the shell layout for members; visitors always get the theme.

## Architecture

MVC with fat models, thin controllers, dumb templates. Server-rendered Jinja (`app/views/` — note `template_folder='views'`) + HTMX + Alpine; no SPA, no npm.

- `app/models/` — business logic, validation, queries. `base.py` has `BaseModel`, `OrgScoped`, `utcnow`.
- `app/controllers/` — thin Flask blueprints. `manage.py` is the org-owner admin, `admin.py` the platform admin, `site.py` the public tenant site, `setup.py` the install wizard.
- `app/platform/` — cross-cutting services: tenant resolution, content types, theming, plugins, jobs queue, storage, mailer, i18n, authz, notifications, newsletter.

**Tenant resolution** (`app/platform/tenant.py`): every request resolves `g.org` from the host — subdomain of `BASE_DOMAIN`, custom domain (`OrgDomain`), or the default org on the bare domain. Runs with `PUBLIC_TENANTS=True`, so `g.org` is set for anonymous visitors too and every query stays scoped; content visibility is a model-layer concern. `g.membership` is the current user's active membership or None. Installation paths (`/setup`, `/admin`, `/auth/`, …) bypass tenancy.

**Content types** (`app/platform/content_types.py`): everything an org publishes is a `Content` row with a `type`. Types are defined **in code** (core or plugins) as `ContentType` dataclasses — labels, public URL base (`/blog`, `/events`), typed `FieldSpec`s, WordPress-style template hierarchy (`single-{type}.html` / `archive-{type}.html`). Adding a vertical means registering one ContentType, never touching the publishing subsystem. "Content" is distinct from a discussion Post (`app/models/discussion.py`).

**Plugins** (`plugins/`, `app/platform/plugins.py`): versioned directories (`glossary/v1/`), registered at boot, gated per-tenant per-request — no restarts to install/uninstall for an org. Plugins can register content types.

**Theming** (`app/platform/theming.py`, `app/views/themes/`): per-org theme selection with template overrides; themes declare editable content fields (`theme_content.py`) so the home page is edited in one place under Manage.

**Jobs** (`app/platform/jobs.py`): DB-backed queue + worker process. Handlers register at import time — `app/__init__.py` imports `notify` and `newsletter` for that side effect.

**Setup & config** (`app/config.py`): layering is defaults → `data/config.env` (written by the setup wizard) → real environment variables (env always wins). An uninitialized install serves only the wizard; `data/` holds the SQLite DB, uploads, and runtime config — `make reset` wipes it.

## Testing conventions

Fixtures in `tests/conftest.py`: `app` (fresh schema per test, tmp `DATA_DIR`), `client`, `user`, `platform_admin`, orgs `acme`/`globex` (two orgs exist so tenant isolation is provable), `login_as(client, user)`. Config is passed into `create_app`, never assigned after. Tests are organized as `test_models/`, `test_controllers/`, `test_platform/`.
