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
| `publication` | The public-facing site: landing page, pages, content archives and singles | The **theme** for site-presented objects (the landing, pages by default, types that declare it); the shell for the rest |
| `application` | The community in use: discussions, members, the member home | **Supremely** (standardized app shell) |
| `console` | `/manage` and `/admin` | Supremely, never themed |

One policy point maps context to a renderer, and objects may declare their
own presentation into it. The community shell serves **everyone** — members
and visitors browse the same surface, with gated content teased in place —
while the theme renders the landing page, previews, and every object that
declares **site presentation**: standalone pages do by default (each page
has an "Appears" choice — on the public site, or inside the community), and
a content type can declare it (the Team roster presents as a themed
brochure page; the blog stays in the shell). The mapping is data, not
scattered conditionals — changing what renders where is a declaration or a
policy edit, not a refactor.

The community shell is app-owned and never theme-resolvable; themes may
tint it only through a small approved token whitelist (brand colors). This
keeps every community's application experience consistent while the public
site can look like anything.

## Theming

Themes are **renderers, not applications**: `theme.json` + Jinja templates
+ static CSS. No Python, no routes, no queries, no permission logic — the
server resolves content and access, then hands the theme what to draw.
Templates resolve through a WordPress-style hierarchy (`front-page.html` →
`archive-{type}.html` → `archive.html` → `single-{item-slug}.html` →
`single-{type}.html` → `single.html` → `errors/{code}.html`, with
`layout/header/footer` parts), falling back to the built-in Origin theme,
so a five-file theme restyles an entire site. Full contract:
[docs/themes/README.md](themes/README.md).

The contract has two halves, and both are documented. A theme declares what
an organizer may customize (`settings`, `content` fields including images
from the media library, `capabilities`, `community_tokens`), validated when
the theme is discovered and when a package is installed. In return the
application guarantees a fixed set of names in every template, of which the
data verbs are `latest_content(type, limit)` and `content_count(type)`:
a theme names a content type and gets published, access-filtered,
eager-loaded rows, so a landing page can grid recent articles or podcast
episodes with no controller aware that theme exists. Asking for a type
nobody publishes returns nothing rather than failing.

Ownership follows one rule: a name, description, logo, favicon or hero
image belongs to the **organization** (Manage → Branding) and survives a
theme change; copy written for a particular layout belongs to the **theme**
(Manage → Theme editor) and does not.

Built-in themes: **Origin** (the default and universal fallback),
**Supremely** (the project's own marketing theme), **Midnight** (a
layout-only dark variant of Origin), **Trailhead** (the worked example from
the tutorial). Origin also ships the fallback templates for library content
types (for example the Team card grid) and the site error page, so every
theme renders every type out of the box and overrides only what it wants to
redesign.

## The community application

Members land in an app shell: a grouped sidebar derived from the org's
content types (Community / Meet / Learn), a home feed of discussion
activity beside the org's latest published content, a New Post modal for
discussions, and a permission-gated Publish menu for authored content.
Object-level management (edit, pin, lock, hide) happens inline: a control
renders whenever the viewer's permissions allow it, and controls a plain
member could never use are visually **marked** rather than hidden behind a
mode. Every action is independently permission-checked server-side.
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
