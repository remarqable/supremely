# Themes — the designer's reference

> **Themes are renderers, not applications.**

A Supremely theme controls how an organization's **public site** looks —
the landing page, pages, and content archives and singles. It is HTML, CSS,
and a small JSON manifest. If you know HTML, CSS, and a simple templating
language (Jinja), you can build one. Start-to-finish walkthrough:
[Building a theme](building-a-theme.md).

## What a theme is

```
themes/yourtheme/
  theme.json          # manifest: name, settings, editable content fields
  layout.html         # the <html> document your pages extend
  header.html         # optional part
  footer.html         # optional part
  front-page.html     # the landing page
  archive.html        # content listings (optional — falls back)
  single.html         # one content item (optional — falls back)
  static/theme.css    # your styles
```

Any template you don't provide falls back to **Origin**, the built-in
default theme — but through *your* `layout.html`, so even a theme with
five files restyles the whole site.

## What a theme is not

Themes must not contain:

- Python or any backend code, routes, or database queries
- permission, membership, or subscription logic
- decisions about who may see something

Supremely resolves content, filtering, ordering, and **access** before your
template runs. You render what you are given. If a design idea seems to
require knowing Supremely's internals, stop — request a presentation object
instead (open an issue).

## Template hierarchy

For each page, Supremely tries templates in specificity order, first in the
active theme, then in Origin:

| Page | Tried in order |
|---|---|
| Landing page | `front-page.html` |
| Standalone page | `page-{slug}.html` → `{template}.html` → `page.html` |
| Content archive (e.g. `/blog`) | `archive-{type}.html` → `archive.html` |
| Content single | `single-{item-slug}.html` → `single-{type}.html` → `single.html` |
| Public discussions | `discussions.html`, `discussion-group.html`, `discussion-post.html` |
| Newsletter | `subscribe.html`, `confirm.html`, `unsubscribe.html` |
| Error pages | `errors/{code}.html` → `errors/error.html` |

Archives and singles are symmetric: `archive-recipe.html` and
`single-recipe.html` are both found from the type's slug, with nothing to
register. Error pages resolve the same way, so a bad URL on your site keeps
your header and footer instead of dropping the visitor onto Supremely
chrome; they receive `code` and, for application errors, a `message`.

Parts resolve the same way: layouts include `{% include themed('header.html') %}`
so a theme may override just a header or footer.

Because a *specific* template anywhere in the chain beats a *generic* one,
Origin's built-in `archive-{type}.html` templates for library content types
(for example `archive-team_member.html`, the Team card grid) render under
**your** layout even when your theme has its own generic `archive.html`.
Every theme gets a working page for every content type; ship the same
filename only when you want a bespoke design for that type.

## Site pages and community pages

Not every URL reaches your templates. The member community (discussions for
members, the member home, the directory) renders in Supremely's app-owned
shell for everyone, visitors included. Your theme renders the **site**
surfaces: the landing page, previews, standalone pages (organizers choose
per page — "on the public site" is the default — under the page's
**Appears** setting), and content types that declare site presentation
(Team is one). You never choose this in the theme; you just provide the
templates.

## What your templates receive

This is the application's half of the contract. Removing a name from these
tables is a breaking change; adding one is not. Anything not listed may
exist and may change without warning — build on what is written down.

**The organization and its assets**

| Name | What it is |
|---|---|
| `g.org` | The organization: `.name`, `.site_name`, `.description`, `.logo()`, `.favicon()`, `.hero_image()`, `.brand_primary` |
| `org_url(org, path)` | Absolute URL on that organization's host, for social meta tags |
| `installation_name` | The installation's name, for operator-level chrome |

**Content and navigation**

| Name | What it is |
|---|---|
| `latest_content(type, limit=None)` | Published items of that type, newest first (see below) |
| `content_count(type)` | How many published items of that type the visitor may see |
| `nav_items('primary')` / `nav_items('footer')` | Navigation configured under Manage → Navigation (`.label`, `.href`, `.is_group`, `.children`) |
| `content_types()` | The content types active for this organization |

**Your own declarations**

| Name | What it is |
|---|---|
| `theme_settings` | Your `theme.json` settings, validated, with org overrides |
| `theme_content()` | Your declared content fields, filled in under Manage → Theme editor |
| `theme_asset('theme.css')` | URL for a file in your `static/` |
| `themed('header.html')` | Resolve a part through the theme chain |
| `theme_capabilities()` / `current_theme()` | Your declared capabilities; the active theme's slug |
| `site_layout` | The resolved layout your page should `{% extends %}` |

**Authorization — to consult, never to enforce**

| Name | What it is |
|---|---|
| `current_user` | The visitor; `.is_authenticated` is the common use |
| `can(permission)` | May this visitor do this — for drawing a control |
| `can_view(object)` | May this visitor read this — for drawing a lock badge |
| `is_org_member()` / `is_member_or_platform_admin()` | Membership checks, for chrome |

**Language and page furniture**

| Name | What it is |
|---|---|
| `_('key')` / `t('key')` | Translation lookup |
| `lang`, `is_rtl` | Active language and direction, for `<html>` |
| `analytics_head()` | The organization's configured analytics tags |
| `plugin_url_for()` | URL building for plugin routes |

### Asking for content

Two verbs, and they are the whole data surface:

```jinja
{% for item in latest_content('article', 3) %}
  <a href="{{ item.permalink }}">
    {% if item.featured_upload %}
      <img src="{{ item.featured_upload.url('thumb') }}"
           alt="{{ item.featured_upload.alt or '' }}">
    {% endif %}
    <h3>{{ item.title }}</h3>
    <p>{{ item.excerpt_or_summary() }}</p>
  </a>
{% endfor %}
```

Name any registered type — `article`, `event`, `episode`, `team_member`, one
a plugin adds years from now — and it works without a line of code written
for your theme. What you can rely on:

- **Empty is normal.** A type nobody publishes, a locked section, a brand
  new site: you get `[]`, never an error. Design your sections to look
  right with nothing in them.
- **Already filtered.** Gated items are removed, or kept as teasers per the
  organization's own setting, before your template runs. You never check.
- **Newest first**, by publication date. (An event feed is therefore
  recently *published* events, not next upcoming ones.)
- **Capped.** There is an internal maximum; a bigger `limit` gets the
  maximum, and omitting `limit` gets it too.
- **One query per question.** Two sections asking for the same list cost one
  query, and the featured image and author come with the rows.

Page-specific context:

| Template | Receives |
|---|---|
| `archive*.html` | `content_type`, `items`, `pagination`, `archive_title` |
| `single*.html`, `page.html` | `content`, `content_type` (`content.title`, `.html`, `.excerpt_or_summary()`, `.fields`, `.author`, `.published_at`) |
| `discussions.html` | `groups`, `recent_posts`, `q` |
| `discussion-group.html` | `group`, `posts`, `q` |
| `discussion-post.html` | `group`, `post`, `top_level`, `children`, `reactions`, `following`, `emoji_set` |

Everything in `items`/`posts`/`groups` is **already authorized and
filtered** for the current visitor. A members-only group simply never
reaches a visitor's template.

## theme.json

```json
{
  "slug": "yourtheme",
  "name": "Your Theme",
  "version": "1.0.0",
  "author": "You",
  "description": "One sentence.",
  "capabilities": {"footer_groups": true},
  "settings": {
    "accent": {"type": "color", "label": "Accent color", "default": "#2d6a4f"}
  },
  "content": {
    "fields": [
      {"key": "headline", "type": "text", "label": "Headline", "max": 100},
      {"key": "subhead", "type": "textarea", "label": "Subheading", "max": 240}
    ]
  }
}
```

- **capabilities** declare what your templates actually render, so Manage
  can warn organizers when an edit won't be visible. Today there is one:
  `footer_groups` — whether your footer shows the organizer's link columns
  (grouped footer menu items) or only the flat bottom-bar links. Omitted
  capabilities default to `true`; declare `false` only for what you
  deliberately leave out (Trailhead's footer, for example, is a single row
  of links, so it sets `"footer_groups": false`).
- **settings** appear under Manage → Settings → Theme. Color values are
  validated server-side before they reach your templates — interpolate them
  into a `<style>` block with confidence.
- **content fields** appear under Manage → Theme editor and come back
  through `theme_content()`. This is how organizers put their own words into
  your design without touching code. Field types: `text`, `textarea`, `url`,
  `image`, and `repeater` (a fixed list of sub-field groups).

An `image` field is picked from the organization's media library and comes
back as the upload itself, so you render it the way you render a content
item's picture:

```jinja
{% set photo = theme_content().hero %}
{% if photo %}<img src="{{ photo.url('full') }}" alt="{{ photo.alt or '' }}">{% endif %}
```

Only public images are offered, because your templates draw public pages.
Alt text is written once under Manage → Media and travels with the file.

### What belongs to you, and what belongs to the organization

The test is simple: **is it an asset or an identity the organization owns,
or is it copy written for your layout?**

| Owner | Examples | Survives a theme change |
|---|---|---|
| Organization | name, site name, description, logo, favicon, hero image | yes — read them from `g.org` |
| Theme | headline, subheading, button labels, closing copy | no, and correctly so |

Use `g.org.site_name` for the name in your header and footer, not
`g.org.name`. They are usually the same string, and differ when a community
and the site in front of it are named separately — a community called "Acme
Community" whose website is simply "Acme". `site_name` falls back to `name`,
so it is always safe to use.

Hero copy stays with the theme because it is written *for a layout*: a
headline composed to sit above your rotating accent words reads as a
fragment in someone else's centred serif hero. A hero *image* is different
in kind — it is a picture the business owns — so it lives on the
organization beside the logo, and a theme reads it with
`g.org.hero_image()`.

Declaring a field that duplicates something the organization already owns
(`brand_name`, `site_name`, `logo`, `description`, …) is refused at
validation. If Supremely already knows it, read it; do not ask an
administrator to retype it.

### Manifest validation

Your `theme.json` is checked when the theme is discovered and when a package
is installed: required keys present, setting types known, content fields
well-formed and carrying a `key`, no field colliding with organization-owned
identity. A built-in theme with a bad manifest fails the build; an installed
theme with one is skipped with a log line, so a third-party theme can never
take an installation down. Either way it never surfaces mid-request.

## Attribution

Every theme's footer carries a "Powered by Supremely" line, and it comes
from an app-owned partial rather than from your markup:

```jinja
{% include 'partials/_powered_by.html' %}
```

All four built-in themes include it in their footer (Midnight builds its
footer into `layout.html`, so it includes it there). If you override
`footer.html`, keep the include — that is what keeps the link and its
tracking parameters defined in one place instead of copied into every theme.
Style the surrounding element however you like; the partial only renders the
text and the link.

## The parts you don't control

- **The community application** (member home, discussions for members,
  member directory) is standardized by Supremely and is not
  theme-resolvable. Your theme may tint it only via `community_tokens` in
  theme.json (whitelisted brand color keys). This is deliberate: every
  community behaves the same inside, while its public face is yours.
- **Administration** (`/manage`, `/admin`) is never themed.

## JavaScript

Optional and deliberately constrained: presentation-level enhancement only.
Most themes need none — Alpine.js is available for dropdowns and toggles.
Never implement application functionality in theme JS.

## Installing

Built-in themes live in `app/views/themes/`. Installations can add themes
under the data volume's `themes/` directory (platform admin → Administration
→ Themes); the org picks a theme under Manage → Settings.

A theme package is a ZIP with `theme.json` at its root. It may contain
templates, styles, scripts, fonts and pictures (`.html`, `.json`, `.css`,
`.js`, `.map`, `.txt`, `.md`, `.svg`, `.png`, `.jpg`, `.jpeg`, `.webp`,
`.gif`, `.ico`, `.woff`, `.woff2`, `.ttf`, `.otf`) — anything else and the
package is refused rather than partly unpacked. Installing a theme is
deploying code, so only platform admins can do it; organizations select from
what is installed.
