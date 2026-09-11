# Themes — the designer's reference

A Supremely theme is HTML, CSS and one small JSON file. If you can build a
web page, you can build a theme. Supremely fetches the content, decides who
may see what, and hands your templates only what the visitor is allowed to
read, so nothing you write can leak a members-only post or break a login.
That is the promise to the organization choosing your theme, and it is why
a theme never contains code.

New here? Build one first: [Building a theme](building-a-theme.md) walks
through a complete theme in an afternoon. This page is the reference you
come back to.

## Anatomy of a page

Every page an organization serves is made of the same parts. A theme owns
the outer ones; the application owns the screens where members talk.

```
┌──────────────────────────────────────────────────────────────┐
│  header.html                       theme  (menu, logo,       │
│                                           member controls)   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  the page                                                    │
│                                                              │
│    a site page ............... theme     front-page.html,    │
│                                          page.html,          │
│                                          archive.html,       │
│                                          single.html         │
│                                                              │
│    a community screen ........ app       discussions, a      │
│      placed by your layout               thread, members,    │
│      beside the rail                     member home         │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  footer.html                       theme                     │
└──────────────────────────────────────────────────────────────┘
```

**Site pages** are yours from the first byte: the landing page, standalone
pages, and the archive and single page of every content type. **Community
screens** are Supremely's. Their markup is the same on every site so a
member who joins a second community already knows how it works. Your theme
decides how they are framed, in one of two ways:

| Mode | What the member sees | Declare it |
|---|---|---|
| **Shell** (default) | Community screens open inside Supremely's own frame, with a navigation column on the left and your brand colours. Your header is not shown there. | nothing to declare |
| **One frame** | Community screens open inside *your* layout. Your header is the navigation, the shell's left column is gone, and the right rail sits where your layout puts it. | `"community_nav": false` in `theme.json` |

Origin, Midnight and Trailhead use the shell. Supremely's own theme uses
one frame, and is the worked example for it. Either way the community
behaves identically; only the frame changes. See
[Framing the community](#framing-the-community) for what one frame asks of
your layout.

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

## Template hierarchy

For each page, Supremely tries templates in specificity order, first in the
active theme, then in Origin:

| Page | Tried in order |
|---|---|
| Landing page | `front-page.html` |
| Standalone page | `page-{slug}.html` → `{template}.html` → `page.html` |
| Content archive (e.g. `/blog`) | `archive-{type}.html` → `archive.html` |
| Content single | `single-{item-slug}.html` → `single-{type}.html` → `single.html` |
| Members-only gate | `gate.html` |
| Newsletter | `subscribe.html`, `confirm.html`, `unsubscribe.html`, `unsubscribed.html` |
| Error pages | `errors/{code}.html` → `errors/error.html` |

Archives and singles are symmetric: `archive-recipe.html` and
`single-recipe.html` are both found from the type's slug, with nothing to
register. Error pages resolve the same way, so a bad URL on your site keeps
your header and footer instead of dropping the visitor onto Supremely
chrome; they receive `code` and, for application errors, a `message`.

Community screens are not in this table on purpose. Discussions, a thread,
the member directory, the member home, the profile and the newsletter
archive are drawn by the application's templates for everyone, visitors
included, and a theme cannot replace them. It frames them (above) and may
restyle them with CSS from its `theme.css`; the class on your `<body>` is
the natural hook.

Parts resolve the same way: layouts include `{% include themed('header.html') %}`
so a theme may override just a header or footer.

Because a *specific* template anywhere in the chain beats a *generic* one,
Origin's built-in `archive-{type}.html` templates for library content types
(for example `archive-team_member.html`, the Team card grid) render under
**your** layout even when your theme has its own generic `archive.html`.
Every theme gets a working page for every content type; ship the same
filename only when you want a bespoke design for that type.

## Framing the community

Which pages a member sees in your theme depends on the mode you declared
above. Standalone pages carry their own choice too: organizers pick, per
page, whether it appears on the site or in the community (the page's
**Appears** setting, "on the public site" by default), and content types can
declare a side (Team presents on the site). You never choose this in a
template; you provide the templates and the pages land where they belong.

### The shell (default)

Do nothing. Community screens render in Supremely's frame. Your theme may
tint that frame through `community_tokens` in `theme.json`, a whitelist of
brand colour keys (`brand-500`, `brand-600`, `brand-700`, as `#RRGGBB`),
and nothing else. Your header should offer members a way in:

```jinja
{% if current_user.is_authenticated and is_org_member() %}
<a href="{{ url_for('orgs.dashboard') }}">Community</a>
{% endif %}
```

### One frame

Declare it in `theme.json`:

```json
"community_nav": false
```

Your `layout.html` now draws every page, and is told which kind it is
drawing through `community_page`. When that is true the page is a community
screen, and your layout must do three things for it:

1. **Give it room.** A discussion list or a thread is not prose; a reading
   column is too narrow. Widen the main column for community pages.
2. **Draw the rail.** The right rail carries the announcement, members,
   upcoming event and pinned posts, and community screens add cards of
   their own by overriding the `rail` block. Import the macro and keep
   `community_rail()` as the block's default:

   ```jinja
   {% from 'partials/_community_rail.html' import community_rail with context %}
   ...
   {% if community_page %}
   <aside id="rail">{% block rail %}{{ community_rail() }}{% endblock %}</aside>
   {% endif %}
   ```

3. **Load HTMX with the CSRF header.** Reactions on a post are swapped in
   place, and the request needs the token:

   ```jinja
   <script src="{{ url_for('static', filename='js/htmx.min.js') }}"></script>
   ...
   <body hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'>
   ```

Your header becomes the community's navigation, so it must carry the
signed-in controls: the way to the console, notifications and the account
menu. They are one application-owned partial, and a theme never rebuilds
them:

```jinja
{% if current_user.is_authenticated %}
  {% include 'partials/_member_controls.html' %}
{% else %}
  <a href="{{ url_for('auth.login') }}">Log in</a>
{% endif %}
```

Put the include where your design wants it; the application keeps what the
controls do and who sees them. `app/views/themes/supremely/layout.html` and
`header.html` show all of this in a shipped theme.

This is a theme declaration rather than an organizer setting because only
the theme knows whether its layout can hold a discussion thread and a rail.
A theme that says nothing keeps the shell, so every theme written before the
key existed is unaffected.

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
| `latest_content(type, limit=None)` | Published items of that type, in the order the type declares (see below) |
| `content_count(type)` | How many published items of that type the visitor may see |
| `nav_items('primary')` / `nav_items('footer')` | Navigation configured under Manage → Navigation (`.label`, `.href`, `.is_group`, `.children`) |
| `content_types()` | The content types active for this organization |

**Your own declarations**

| Name | What it is |
|---|---|
| `theme_settings` | Your `theme.json` settings, validated, with org overrides |
| `theme_content()` | Your declared content fields, filled in under Manage → Theme editor |
| `render_fields(item, surface='web')` | The fields that item's type declares, drawn as HTML. `surface='summary'` for a listing card |
| `render_lead_field(item)` | The one field the type leads its listing card with (a date block, say), or nothing |
| `site_entries()` | The content types this organization advertises on its front page, in its chosen order |
| `site_feed_template(type)` | Which partial draws one of those sections, resolved through your theme first |
| `item.visible_children()` | The blocks written inside an item that this visitor may read |
| `blocks_template()` | Which partial draws the whole run of blocks under an item's body |
| `block_template(block)` | Which partial draws one block, resolved through your theme first |
| `embed_template(item)` | Which partial draws an item pulled into a body by `:::embed` |
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
| `single*.html`, `page.html` | `content`, `content_type` (`content.title`, `.html`, `.excerpt_or_summary()`, `.author`, `.published_at`; call `render_fields(content)` for the type's own fields) |
| `gate.html` | `gate_title`, `gate_kind`, `gate_teaser`, `login_next` |
| `layout.html` | `community_page`: true while framing a community screen (only with `"community_nav": false`) |

Everything in `items` is **already authorized and filtered** for the current
visitor. A members-only item simply never reaches a visitor's template.


## Field partials

A content type declares typed fields — a video's URL, an event's date. Call
`render_fields(item)` and each one is drawn by a partial chosen from its
type and key, so your template never has to know which types have which
fields.

Override one by shipping a file. Most specific wins:

```
fields/url-video_url.html     this key on this type
fields/url.html               every URL field
fields/_default.html          anything with no partial of its own
```

Yours are looked at before the ones Supremely ships, so `fields/url.html`
in your theme replaces every URL field including the video embed. `_default`
is the exception: it is consulted after every typed partial anywhere, so
adding one gives you a fallback without switching off the players.

Four surfaces, and a partial is per surface:

| Directory | Drawn where |
|---|---|
| `fields/` | the item's own page |
| `fields/summary/` | a listing card, inline and unlabelled |
| `fields/lead/` | the block a card leads with, in place of the author avatar |
| `fields/email/` | a newsletter. Inline styles only: no stylesheet reaches an email client, and these never fall back to your web partials |

Each partial receives `value`, `label`, `spec` and `content`, plus `_()` for
translation. They are trusted template output rather than sanitized Markdown,
which is how a video field can emit an `<iframe>` when a body never can —
and why a partial must never put a value into markup unescaped. Use
`safe_url(value)` for anything that becomes an `href` or a `src`.


## The front page window

A community's content lives in the community. The public site advertises it:
a section per content type, linking inward. There is no second address for
an item, so nothing is published twice and a search engine has nothing to
choose between.

Which types appear, and in what order, is the organization's decision
(Manage -> Home page). Nothing appears until somebody opens a window, so a
front page you designed stays as you designed it.

Your front page asks for the sections and draws them:

```html
{% for content_type in site_entries() %}
{% include site_feed_template(content_type) with context %}
{% endfor %}
```

Each section partial receives `content_type`, and may set `limit` before
including to ask for a different number of items. Override one type's
section by shipping `site-feed-{type}.html`, or all of them with your own
`_site_feed.html`; both resolve through your theme before the defaults, and
a `mobile/` sibling of either is picked up on a phone.

Inside a section, `latest_content(slug, limit)` and `content_count(slug)`
give you the items and the total. Both already account for who is looking:
a members-only item arrives as a locked title where the organization teases
its gated content, and is simply absent where it does not. Nothing in your
template decides who may read anything.


## Blocks inside an item

Some content is written inside other content: a recipe card in an article, a
lesson in a course. A block is an ordinary content row with a parent, so it
has the same fields, the same renderer and the same visibility rules as
anything else. It simply has no address of its own, and appears in no
archive, feed or count.

Your single and page templates draw them after the body:

```html
{% include blocks_template() with context %}
```

Override the whole section with your own `_content_blocks.html`, or one
type's block with `content-block-{type}.html`. Both resolve through your
theme first, and a `mobile/` sibling of either is picked up on a phone.

If you replace the wrapper, call the loop variable `block` — that is the
name a block partial reads:

```html
{% for block in content.visible_children() %}
{% include block_template(block) with context %}
{% endfor %}
```

Two things are already decided before your template runs.
`item.visible_children()` has applied the organization's gating rules, so a
members-only lesson arrives as a locked title where that organization teases
its gated content and is absent where it does not; and blocks arrive in the
order their author put them in. Ask `can_view(block)` before drawing a body,
exactly as you would for any item in a list. Nothing in a template decides
who may read what.

Blocks go one level deep, and they render after the body rather than
somewhere inside it. Both are deliberate: blocks are content, not layout,
and Supremely is not a site builder.


## Directives in a body

An author can reference other content from inside a body. A directive is a
paragraph of its own and nothing else:

```
:::video https://www.youtube.com/watch?v=...   a player
:::image 42 left                               a picture from the library
:::embed episode/why-we-build                  one published item
:::feed episode limit=3                        a type's latest items
```

The first two resolve in any body, including a discussion post, because
each reaches no further than markup built here from a value parsed here.
They land as markup your prose styles will meet, so they are worth knowing
about:

| Directive | What it leaves in the body |
|---|---|
| `:::video` | `<div class="video-embed">` around an `iframe`, sized 16:9 |
| `:::image` | `<figure class="body-image body-image--center\|left\|right\|wide">` around an `img` |

`iframe` and a `class` on a `figure` are both outside the Markdown
allowlist on purpose: one renderer serves editorial content and member-written
discussion posts alike, so this markup is built after sanitizing rather than
trusted from what somebody typed. The application styles both, and a theme
that wants its own look styles those class names rather than replacing the
markup.

The last two are the ones with a reader and an access decision behind them:

Anything else on the line is not a directive, and a directive in backticks
or in a code block is code, which is where anyone writing *about* the syntax
would put it.

The first part is the content type. Either its slug or the address its
archive lives at will do, so `episode/why-we-build` and
`podcast/why-we-build` find the same item. The second spelling is the one an
author reads off the address bar, and no type in the library has a base
equal to its slug.

`:::feed` draws the **same partial as the front page window**, so a section
an author places mid-article and one a theme places on the front page are
the same thing: override `site-feed-{type}.html` or `_site_feed.html` and
both change together. `:::embed` has its own seam — ship `embed-{type}.html`
for one type or `_embed.html` for all of them.

An embed partial receives `item`, `content_type`, and `locked`. Draw the
body from `item.html_flat`, never `item.html`: the flat form leaves any
directives inside the embedded item as plain text, which is what stops an
embed following what it pulled in. The renderer enforces that too, so
getting it wrong costs you a missing embed rather than a hung server.

As everywhere else, the access decision is already made. A gated target
arrives as `locked` where the organization teases its gated content and
never arrives at all where it does not, and its body is not rendered either
way. A whole section that is members-only renders nothing rather than a
locked title, the same as its archive does: one gate, no item titles teased.

A directive that cannot be honoured leaves nothing behind. That covers a
typo in the target, a draft, a type the organization does not publish, and
a body that has used its ten. Ten is the cap, because each directive is a
query and a template render, and no page should be able to cost thousands
of either. Bodies are typed by hand, so a mistake leaves a gap rather than
an error message or a line of raw `:::embed` in a published page.

Two places directives are inert by design. They do nothing in **discussion
posts and replies**, which share this renderer but are written by any member
rather than by somebody publishing the site. And they do nothing in a
**newsletter**, which is sent by a background job with no reader to answer
`can_view` for; the prose around them still sends.

## theme.json

```json
{
  "slug": "yourtheme",
  "name": "Your Theme",
  "version": "1.0.0",
  "author": "You",
  "description": "One sentence.",
  "community_nav": true,
  "community_tokens": {"brand-600": "#2d6a4f"},
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
- **community_nav** says whether community screens render inside the
  app-owned shell (`true`, the default, and what you get by leaving it out)
  or inside your own layout (`false`; see [Framing the
  community](#framing-the-community) for what your layout then has to do).
- **community_tokens** tint the community shell with your brand when you
  keep it (`community_nav` true). Three keys are accepted, `brand-500`,
  `brand-600` and `brand-700`, each an `#RRGGBB` colour; anything else is
  ignored. Leave it out and the shell uses the organization's own brand
  colour.
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

## Mobile

Your templates are responsive: one file, Tailwind's `sm:` / `md:` / `lg:`
prefixes, and a phone gets the same page rearranged. That is the default and
it is what almost every surface should do.

When a phone genuinely needs a different layout — not a narrower one, a
different one — add a mobile sibling of the template under `mobile/`:

```
themes/yourtheme/
  front-page.html
  single.html
  header.html
  mobile/
    front-page.html     # only used on phones
    header.html         # swap just the header if that is all that differs
```

Anything you do not provide falls back exactly as it always did: no
`mobile/` directory at all is the normal case, and a phone then renders your
ordinary template. A mobile variant replaces only the template it is the
mobile version of, so `mobile/single.html` never displaces a more specific
template further down the chain.

To see a mobile template on a desktop browser, add `?device=mobile` to any
URL; `?device=desktop` pins the other way and `?device=auto` goes back to
detection.

## Two includes belong in your head

Both go just before `</head>`, and Origin's layout has them if you ship no
layout of your own:

```jinja
{% include 'partials/_head_links.html' %}
{% include 'partials/_analytics.html' %}
```

`_analytics.html` renders whichever tracker the organization configured
under Manage → Settings → Analytics, and nothing at all when analytics is
off. Leave it out and sites using your theme silently lose their visitor
statistics.

`_head_links.html` renders the `<link rel="alternate">` tags pointing at the
site's RSS and Atom feeds, and at the current section's own feeds when the
page is an archive. Leave it out and the feeds still answer at their
addresses, but nothing on the page advertises them, so a reader's subscribe
button finds nothing.

## Previews

An organizer can preview a draft before publishing it, and the preview
renders through your theme. Two things belong in your `layout.html`, and
only there. If you ship no layout of your own, Origin's does both and you
have nothing to do.

First, the banner that says the draft is not published, between your
header and your content:

```jinja
{% include 'partials/_preview_banner.html' %}
```

Put it outside every `{% block %}`. A page template that overrides one of
your blocks would otherwise drop it — which is exactly how it once went
missing from Supremely, whose page templates override the layout's `main`.

It renders once per request, so you do not have to worry about the content
templates you inherit from Origin including it as well. Between the two, a
theme that overrides only its layout and a theme that overrides only its
content templates are both covered.

Second, switch your navigation off while a preview is on screen, so a click
on your header does not carry the organizer off the draft they opened:

```jinja
{% if preview %}<div inert class="contents">{% endif %}
{% include themed('header.html') %}
{% if preview %}</div>{% endif %}
```

The same around your footer. `inert` is an ordinary HTML attribute: it stops
clicks, takes the links out of the tab order, and hides them from screen
readers, with no script involved. The wrapper is `contents` so it draws no
box of its own and your header keeps whatever positioning it had, sticky
included.

Both go in the layout because they belong together, and the banner goes
first because it is the way out: it carries the link back to the editor, and
it must not be inside the region you just switched off. A layout that has
neither is merely unlocked; one that has only the lock strands the reader on
a page where nothing goes anywhere. Neither renders anything outside a
preview, so both are safe to include unconditionally.
`tests/test_platform/test_theme_contract.py` checks each bundled theme for
both, on an article and on a page, and checks that a theme it has never
heard of still gets the banner from the templates it inherits.

## Attribution

Every page carries a "Powered by Supremely" line: every theme's footer, and
the community shell when a theme keeps it. It comes from an app-owned
partial rather than from your markup:

```jinja
{% include 'partials/_powered_by.html' %}
```

All four built-in themes include it in their footer (Midnight builds its
footer into `layout.html`, so it includes it there). If you override
`footer.html`, keep the include — that is what keeps the link and its
tracking parameters defined in one place instead of copied into every theme.
Style the surrounding element however you like; the partial only renders the
text and the link.

## The rules

Everything above is what you can do. This is the short list of what a theme
never does, kept together so it is easy to check against:

- **No code.** No Python, routes, or database queries. A theme is templates,
  styles and static files.
- **No access decisions.** Never check a permission, membership or
  subscription in a template. If something reached your template, the
  server already decided the visitor may see it. If a design idea seems to
  need Supremely's internals, stop and open an issue asking for a
  presentation object instead.
- **The community screens and the console are not yours to redraw.** You
  frame the first (see above) and never touch the second: `/manage` and
  `/admin` are never themed.
- **Ask, don't reach.** Content comes through `latest_content`,
  `content_count`, `site_entries()` and the other documented names. Nothing
  outside those tables is a stable surface.

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
