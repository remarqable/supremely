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
| Content single | `single-{slug}.html` → `single-{type-template}.html` → `single.html` |
| Public discussions | `discussions.html`, `discussion-group.html`, `discussion-post.html` |
| Newsletter | `subscribe.html`, `confirm.html`, `unsubscribe.html` |

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

Common context (always available):

| Name | What it is |
|---|---|
| `g.org` | The organization: `.name`, `.description`, `.logo()`, `.favicon()`, `.brand_primary` |
| `nav_items('primary')` / `nav_items('footer')` | Navigation configured under Manage → Navigation (`.label`, `.href`, `.is_group`, `.children`) |
| `theme_settings` | Your `theme.json` settings, validated, with org overrides |
| `theme_content()` | Your declared content fields, filled in under Manage → Home page |
| `theme_asset('theme.css')` | URL for a file in your `static/` |
| `_('key')` | Translation lookup |
| `site_layout` | The resolved layout your page should `{% extends %}` |

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
- **content fields** appear under Manage → Home page and come back through
  `theme_content()`. This is how organizers put their own words into your
  design without touching code.

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
