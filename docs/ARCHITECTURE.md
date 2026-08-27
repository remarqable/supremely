# Architecture

This is the public map of how Supremely actually works — the running system,
not an aspiration. It is written for contributors, self-hosters, plugin
authors, and theme designers. For hands-on theme work, start with
[docs/themes/](themes/README.md).

## The shape of the system

Supremely is a multi-tenant Flask application. One codebase serves
self-hosted installations, third-party SaaS providers, and the official
hosted service. The tenant is the **Organization**: a community with a
public website, members, published content, discussions, and a newsletter
audience.

```
Request → tenant resolution → access policy → presentation context → HTML
```

- **Tenant resolution** (`app/platform/tenant.py`): every request resolves
  the organization from the hostname — a subdomain of the base domain, a
  custom domain, or the bare domain when a single organization exists.
  `g.org` is set even for anonymous visitors, and a global SQLAlchemy filter
  scopes every query to the tenant. Business models never filter by `org_id`
  by hand.
- **Access policy** lives on objects (see *Visibility*, below) and is
  enforced in controllers before anything renders.
- **Presentation context** decides how an authorized object appears (see
  *Presentation*, below).

## One content graph

There is no separate "website content" and "community content" — they are
the same objects.

**Published content** is a single `Content` model typed by a registry
(`app/platform/content_types.py`). A content type declares its labels, its
public URL base (`/blog`, `/events`, `/recordings`), its structured fields
(a recording has a video URL and duration; an event has a date and
location), and which sidebar section of the community it belongs to
(Community / Meet / Learn). Core ships `page`, `article`, `event`; the
library (`app/platform/content_library.py`) adds `announcement`,
`recording`, `episode`, and `resource`; plugins can register more. Plugin
types are registered globally at boot but **surface only in organizations
where that plugin is enabled**.

**Discussions** are a separate model family (`app/models/discussion.py`):
`DiscussionGroup → Post → Reply`, with reactions, follows, pinning,
locking, and moderation flags. Vocabulary is deliberate: a *post* is what a
member writes in a discussion group; published content goes by its type
name (an article, a recipe). The word "post" never refers to published
content anywhere in the product.

## Visibility: who may see an object

Access lives **on the object**, independent of how it is presented:

- `Content.visibility` and `DiscussionGroup.visibility` — `public` or
  `members` (`restricted` is reserved for future group/paid access).
- Enforcement is server-side and central (`visible_to_current_visitor`,
  `readable_by_current_visitor`, `app.platform.authz.can_view`). Rendering
  happens only after authorization. **Themes never decide access.**

This makes mixed communities natural: public stories and events beside
members-only discussions, with sign-up as the gate to participation — all
configuration, no code.

## Presentation: how an object appears

Every render through `render_site()` (`app/platform/theming.py`) declares a
**presentation context**:

| Context | Surface | Controlled by |
|---|---|---|
| `publication` | The public-facing site: landing page, pages, content archives and singles | The organization's **theme** |
| `application` | The community in use: discussions, members, the member home | **Supremely** (standardized app shell) |
| `console` | `/manage` and `/admin` | Supremely, never themed |

One policy point maps context and viewer to a renderer. Visitors always get
the theme; members get the standardized community shell on application
surfaces. The mapping is data, not scattered conditionals — changing what
members see is a policy edit, not a refactor.

The community shell is app-owned and never theme-resolvable; themes may
tint it only through a small approved token whitelist (brand colors). This
keeps every community's application experience consistent while the public
site can look like anything.

## Theming

Themes are **renderers, not applications**: `theme.json` + Jinja templates
+ static CSS. No Python, no routes, no queries, no permission logic — the
server resolves content and access, then hands the theme presentation
objects. Templates resolve through a WordPress-style hierarchy
(`front-page.html` → `archive-{type}.html` → `archive.html` →
`single-{slug}.html` → `single.html`, with `layout/header/footer` parts),
falling back to the built-in Origin theme, so a five-file theme restyles an
entire site. Full contract: [docs/themes/README.md](themes/README.md).

Built-in themes: **Origin** (the default and universal fallback),
**Supremely** (the project's own marketing theme), **Trailhead** (the
worked example from the tutorial). Themes declare editable content fields
(the landing hero) that organizers fill in under Manage → Home page.

## The community application

Members land in an app shell: a grouped sidebar derived from the org's
content types (Community / Meet / Learn), a home feed of discussion
activity beside the org's latest published content, a New Post modal for
discussions, and a permission-gated Publish menu for authored content.
Object-level management (edit, pin, lock, hide) happens inline behind a
**manage-mode** toggle — a presentation state, never an authorization
state; every action is independently permission-checked server-side.
`/manage` remains the console for configuration, queues, and bulk work.

## Plugins

Plugins live in versioned directories (`plugins/<slug>/v<major>/`),
register at boot (blueprints, content types, translations), and are gated
per-organization per-request — installing or disabling a plugin for one
org requires no restart. Plugin content types ride the same registry and
the same per-org gating as everything else.

## Platform services

- **Authentication**: password-based; everything — install, publish,
  invite, recover — works with no email service configured. Email enhances,
  never gates.
- **Jobs**: a database-backed queue (`app/platform/jobs.py`) with a
  separate worker process (`flask jobs run`). Newsletter delivery and
  notification emails run there, idempotently.
- **Database**: SQLite by default, PostgreSQL via one `DATABASE_URL`
  variable. Dev builds schema straight from models; production applies
  Alembic migrations at container start (single migrator; the worker waits).
- **Deployment**: a Docker image running web (Gunicorn) and worker
  containers, fronted by Caddy with automatic TLS; a one-command installer
  script provisions and updates a VPS.

## Decisions

Material decisions are recorded in [`docs/adr/`](adr/).
