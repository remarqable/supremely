# Building a theme, start to finish

We're going to build a complete custom theme for a fictitious community —
**Trailhead Collective**, a hiking club — and along the way configure a
community where:

- the **landing page is public** and beautiful,
- **stories (articles) and events are public** — anyone can read,
- **joining is required to participate** — commenting and posting need an
  account,
- **discussions are members-only**, except a public Welcome group that
  visitors can read (but not reply to) as a taste of the community.

The finished theme ships with Supremely as `app/views/themes/trailhead/` —
every snippet below is real, tested code you can open and run.

One idea drives everything: **access is configured on objects; themes only
render.** You will not write a single permission check.

---

## Part 1 — Plan the access map (no code)

Before any template, decide who sees what. All of this is configuration
under Manage — the theme inherits it for free:

| Thing | Visibility | Where it's set |
|---|---|---|
| Landing page | public | always public |
| Stories (`article` content) | public | per-item, in the editor |
| Events | public | per-item, in the editor |
| Welcome group | public (read-only for visitors) | Manage → Groups |
| Trail Talk, other groups | members | Manage → Groups |
| Replying / posting anywhere | members | built-in: participation always requires an account |

The gate you want — "sign up to comment" — is server behavior: a visitor
reading a public discussion sees the built-in *log in to participate*
path. Your theme never checks anything.

## Part 2 — Scaffold

A theme is a folder with a manifest:

```
app/views/themes/trailhead/
  theme.json
  layout.html
  header.html
  footer.html
  front-page.html
  static/theme.css
```

Notice what's *missing*: no archive or single templates. Pages we don't
provide fall back to the Origin theme's bodies — rendered inside **our**
layout — so five files restyle the entire site. We can specialize later.

`theme.json` declares identity, two color **settings** (organizer-tweakable
under Manage → Settings → Theme), and the **content fields** of our landing
hero (edited under Manage → Theme editor → Home page):

```json
{
  "slug": "trailhead",
  "name": "Trailhead",
  "version": "1.0.0",
  "settings": {
    "forest": {"type": "color", "label": "Forest (accent)", "default": "#2d6a4f"},
    "bark":   {"type": "color", "label": "Bark (headings)", "default": "#40241a"}
  },
  "content": {
    "fields": [
      {"key": "headline",  "type": "text",     "label": "Headline",   "max": 100},
      {"key": "subhead",   "type": "textarea", "label": "Subheading", "max": 240},
      {"key": "cta_label", "type": "text",     "label": "Button label", "max": 40,
       "default": "Join the community"},
      {"key": "cta_url",   "type": "url",      "label": "Button link", "max": 200,
       "default": "/auth/register"}
    ]
  }
}
```

## Part 3 — The layout

`layout.html` is the document every page extends. Two things to notice:
settings arrive as `theme_settings` (already validated server-side) and
become CSS variables; and the layout includes header/footer through
`themed()`, so those parts also resolve theme-first:

```html
<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ 'rtl' if is_rtl else 'ltr' }}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}{{ g.org.name }}{% endblock %}</title>
  <link href="{{ url_for('static', filename='css/app.css') }}" rel="stylesheet">
  <link href="{{ theme_asset('theme.css') }}" rel="stylesheet">
  <style>
    :root {
      --color-brand-600: {{ theme_settings.get('forest', '#2d6a4f') }};
      --trail-heading:   {{ theme_settings.get('bark', '#40241a') }};
    }
  </style>
  {% include 'partials/_analytics.html' %}
</head>
<body class="trailhead min-h-screen bg-[#faf7f2] font-sans text-slate-800">
  {% include themed('header.html') %}
  <main class="mx-auto max-w-4xl px-4 py-10">
    {% include 'partials/_flash.html' %}
    {% block content %}{% endblock %}
  </main>
  {% include themed('footer.html') %}
</body>
</html>
```

The `partials/_analytics.html` include is part of the layout contract: it
renders the analytics tracker the organizer configured under
Manage → Settings → Analytics (and renders nothing when analytics is off).
Every theme's `layout.html` must include it just before `</head>`, or sites
using your theme silently lose their visitor stats.

`header.html` renders the navigation the organizer configured — you never
hardcode a menu — and shows how a theme adapts to the *viewer* without
checking permissions itself (these globals are provided):

```html
{% for item in nav_items('primary') if not item.is_group %}
<a href="{{ item.href }}">{{ item.label }}</a>
{% endfor %}
{% if current_user.is_authenticated and is_org_member() %}
<a href="{{ url_for('orgs.dashboard') }}" class="btn-primary">Community</a>
{% elif not current_user.is_authenticated %}
<a href="{{ url_for('auth.login') }}">Log in</a>
{% endif %}
```

## Part 4 — The landing page

`front-page.html` renders the hero from your declared content fields. The
organizer writes the words; you own the design:

```html
{% extends site_layout %}
{% block content %}
{% set hero = theme_content() %}
<div class="trail-hero rounded-2xl px-6 py-20 text-center">
  <h1 class="font-serif text-5xl font-bold text-[var(--trail-heading)]">
    {{ hero.headline or g.org.name }}</h1>
  {% if hero.subhead or g.org.description %}
  <p class="mx-auto mt-5 max-w-xl text-lg text-slate-600">
    {{ hero.subhead or g.org.description }}</p>
  {% endif %}
  {% if hero.cta_label %}
  <a href="{{ hero.cta_url or '/auth/register' }}" class="btn-primary mt-8">
    {{ hero.cta_label }}</a>
  {% endif %}
</div>
{% endblock %}
```

### Showing the newest stories

A landing page that is only hero copy gets stale. Ask for content by name
and Supremely fetches it — you never write a query:

```html
{% if content_count('article') %}
<h2 class="font-serif text-3xl">From the trail</h2>
<div class="mt-6 grid gap-6 sm:grid-cols-3">
  {% for story in latest_content('article', 3) %}
  <a href="{{ story.permalink }}" class="block">
    {% if story.featured_upload %}
    <img src="{{ story.featured_upload.url('thumb') }}"
         alt="{{ story.featured_upload.alt or '' }}" class="rounded-lg">
    {% endif %}
    <h3 class="mt-3 font-semibold">{{ story.title }}</h3>
    <p class="text-sm text-slate-600">{{ story.excerpt_or_summary(120) }}</p>
  </a>
  {% endfor %}
</div>
{% endif %}
```

`latest_content` takes any registered type — swap `'article'` for
`'event'`, `'team_member'`, or a type a plugin adds later, and the same
three lines work. A club with nothing published yet gets an empty list, not
an error, which is why the section is wrapped in `content_count` rather
than a guard you have to invent. And gating happens before you see the
rows: a members-only story is filtered out, or teased, according to the
organizer's setting.

## Part 5 — Stories and events, free of charge

We shipped no `archive.html` or `single.html`, yet `/blog` and `/events`
already work in Trailhead's chrome: the template hierarchy fell back to
Origin's bodies inside our layout. The same holds for library content
types — enable the Team type and `/team` renders Origin's card grid
(`archive-team_member.html`) in your theme without a line of work. When
you want full control, add your own — for example, an events archive with
date styling:

```html
{# archive-event.html — only events use this; /blog keeps the fallback #}
{% extends site_layout %}
{% block content %}
<h1 class="font-serif text-4xl text-[var(--trail-heading)]">{{ archive_title }}</h1>
{% for item in items %}
  <a href="{{ item.permalink }}">{{ item.title }}</a>
  <span>{{ item.fields.get('starts_on') }} · {{ item.fields.get('location') }}</span>
{% else %}
  <p>Nothing scheduled yet.</p>
{% endfor %}
{% endblock %}
```

Everything in `items` is already filtered for the current visitor — a
members-only item simply isn't in the list.

## Part 6 — The public community teaser

Visitors hitting `/discussions` get the theme's `discussions.html`
(Origin's, unless you override it), showing only **public** groups — your
members-only groups never reach a visitor's template. A visitor opening a
public post can read it; the reply form is replaced by the built-in
*log in to participate* link. Members never see any of this: for them,
discussions are the standardized Supremely application, which themes do
not control. That split — themed publication outside, consistent app
inside — is the platform's design, and your theme gets it for free.

## Part 7 — Install, activate, verify

```bash
make demo                      # fresh community, admin@demo.test / password
make run
```

Log in as the owner → Manage → Settings → Theme → **Trailhead** → Save.
Then Manage → Theme editor → Home page to write the hero. Verification checklist:

| As a visitor (private window) | Expect |
|---|---|
| `/` | Trailhead hero, your words |
| `/blog`, `/events` | readable, Trailhead chrome |
| `/discussions` | public groups only, no members-only groups |
| open a public post → reply | sent to log in |
| As a member (`member@demo.test`) | the standardized community app, not your theme |

## Part 8 — Rules of the road

- No Python, routes, queries, or permission logic in themes — ever.
- Never gate content in a template; if you can render it, the server
  already authorized it.
- JavaScript is optional presentation enhancement, not functionality.
- Ask for content with `latest_content` / `content_count`; never reach past
  them into models or the session.
- If a design needs something the documented context doesn't provide, open
  an issue — don't reach into internals.

That's the whole contract. Five files, one afternoon, a completely
different website — and not one access check.
