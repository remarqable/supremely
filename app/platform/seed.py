"""Dogfood seed: supremely.org built on Supremely itself.

Demonstrates pages, theme, navigation, articles, documentation content,
newsletter signup, community discussions, and membership -- entirely through
the same models any organization uses. Idempotent.
"""

from app.extensions import db
from app.platform.logger import get_logger

log = get_logger()

ABOUT_BODY = """\
## Why Supremely exists

Publishing platforms make you choose: own your website, own your audience,
or own your community. Supremely refuses the choice.

The project is developed AI-first and built in public. Humans own
architecture, security, review, and what ships.

## Principles

1. One codebase for self-hosted, SaaS providers, and the official cloud
2. Multi-tenant from day one — the Organization is the tenant
3. Email enhances, never gates
4. Familiar ecosystem concepts: themes, templates, content types, plugins, hooks
"""

DOCS_BODY = """\
## Getting started

```bash
git clone https://github.com/remarqable/supremely
cd supremely
docker compose up
```

Open the printed URL and complete the setup wizard: environment, database
(SQLite or PostgreSQL), your Platform Admin, optional email, and your first
Organization.

## Concepts

| Concept | What it is |
|---------|-----------|
| Organization | The tenant: a website/community with members |
| Page | Durable website content (About, Pricing, Docs) |
| Content | The universal publishing unit; typed via Content Types |
| Group | A discussion area holding posts and replies |
| Theme | A per-organization presentation package |
| Plugin | Boot-loaded code, enabled per organization |

## Operating without email

Everything works with no SMTP: password auth, CLI recovery
(`flask users reset-password`), copyable invitation links, and web
publishing. Configure email later in Administration to unlock invitations
by mail, notification copies, and newsletters.
"""

TERMS_BODY = """\
These Terms of Service govern your use of this site and community.

## Placeholder

This page was created automatically so the footer's legal links work from
day one. Replace it with your real terms under **Manage → Content**.

- Be respectful in discussions.
- Content you post remains yours; you grant us a license to display it here.
- Accounts that break the rules may be suspended.
"""

PRIVACY_BODY = """\
This Privacy Policy describes what we collect and how we use it.

## Placeholder

This page was created automatically so the footer's legal links work from
day one. Replace it with your real policy under **Manage → Content**.

- We store the account details you give us (name, email) to run the community.
- Newsletter subscriptions are double opt-in where email is configured.
- We do not sell your data.
"""

GET_BODY = """\
## Self-host it today

Supremely is open source under the AGPL-3.0 license. The code lives on
[GitHub](https://github.com/remarqable/supremely) — read it, run it, star it.

```bash
git clone https://github.com/remarqable/supremely
cd supremely
docker compose up
```

See the [documentation](/docs) for the full install and deployment guide.

## Hosted Supremely — coming soon

We are building an official hosted service for people who would rather not
run a server. It is not available yet.

[Subscribe](/subscribe) to hear when it launches.
"""

# The words of the press kit. The designed chrome (asset cards, brand
# swatches, type specimen) lives in the theme's page-presskit.html; this
# body is what an organizer edits under Manage → Content.
PRESSKIT_BODY = """\
## Approved copy

**One-liner.** Supremely is the open-source platform for publishing,
memberships, newsletters, and community.

**Short paragraph.** Supremely is an open-source, multi-tenant platform for
publishing, memberships, newsletters, and community. Publishing platforms
make you choose between owning your website, your audience, or your
community — Supremely refuses the choice. One codebase serves self-hosted
installs, third-party providers, and the official hosted service.

**Full description.** Supremely is an open-source platform (AGPL-3.0) for
communities that publish. An Organization — the tenant — gets a website,
member accounts, discussions, newsletters, and typed content, served by one
codebase that runs the same whether self-hosted on a single server or
operated as a hosted service. Email is optional infrastructure throughout:
installing, publishing, and onboarding never require it. The project is
developed AI-first and built in public; humans own architecture, security,
review, and what ships.

## Key facts

- **Product:** Supremely
- **Company:** remarQable LLC
- **License:** AGPL-3.0 (open source)
- **Model:** free to self-host; official hosted service coming soon
- **Website:** [supremely.org](https://supremely.org)
- **Source:** [github.com/remarqable/supremely](https://github.com/remarqable/supremely)

## Media contact

[dev@supremely.org](mailto:dev@supremely.org)

Please write the name as **Supremely** — capital S, one word — and use the
logos below as provided: no recoloring, stretching, or effects.
"""

_COMPARISON_FOOTER = """\

---

*This is an early comparison written by the Supremely team, about products we
respect. Spot something wrong or outdated?
[Tell us in the community](/discussions).*
"""

# Honest, qualitative comparisons — model and architecture differences only,
# no pricing or feature-matrix claims that go stale.
COMPARISONS = (
    ('vs-ghost', 'Supremely vs Ghost', """\
Ghost is the closest neighbor to Supremely: open-source publishing with
newsletters and paid memberships, and the product we would recommend most
readily if Supremely didn't exist.

## What Ghost is great at

A mature, polished editor and publishing experience, first-class newsletters
and paid memberships, a large theme ecosystem, and years of production
hardening.

## Where Supremely differs

- **Multi-tenant by design.** A Ghost install serves one site; one Supremely
  install serves many organizations, each with its own site, members, and
  domain.
- **Community built in.** Discussions, member directories, and groups are
  part of the platform, not an integration.
- **Email is optional.** A Supremely install is fully usable — publishing,
  members, discussions — before any email provider is configured.
- Both are open source; Supremely is AGPL-3.0 and free to self-host.

## Which should you pick?

If you want the most mature open-source publishing product available today,
pick Ghost. If you want publishing, newsletters, *and* a real community in
one self-hostable, multi-tenant platform, pick Supremely.
"""),
    ('vs-wordpress', 'Supremely vs WordPress', """\
WordPress runs a huge share of the web, and for a general-purpose website
with an unmatched plugin and theme ecosystem it is still the default answer.

## What WordPress is great at

Ubiquity. Endless themes, plugins, page builders, agencies, and hosts — if
you can imagine a website, someone has built it on WordPress.

## Where Supremely differs

- **One coherent model instead of bolt-ons.** Memberships, newsletters, and
  community on WordPress come from separate plugins with separate data
  models that have to be stitched together. In Supremely they are one
  system out of the box.
- **Multi-tenant by design.** One install, many organizations — WordPress
  multisite exists, but tenancy is Supremely's foundation, not an add-on.
- **A smaller, deliberate surface.** Fewer moving parts to secure, update,
  and keep compatible with each other.

## Which should you pick?

If you need the vast ecosystem or a site builder, WordPress has no equal.
If the point of your site is members, content, and conversation working as
one product, pick Supremely.
"""),
    ('vs-substack', 'Supremely vs Substack', """\
Substack made paid newsletters effortless: sign up, write, publish, charge —
with a discovery network on top.

## What Substack is great at

The fastest possible start for a paid newsletter, zero infrastructure, and
a reader network that can genuinely help you grow.

## Where Supremely differs

- **You own the platform.** Supremely is open source and self-hostable —
  your domain, your database, your member relationships, exportable and
  portable because they live on your server.
- **More than a newsletter.** Your audience gets a home: published content,
  discussions, and membership, with the newsletter as one part.
- **No platform intermediary.** Nobody sits between you and your audience,
  and no platform-wide policy change can reprice or reshape your business.

## Which should you pick?

If you want to start a paid newsletter this afternoon with discovery built
in, Substack is honestly hard to beat. If you're building a lasting home
for an audience you own, pick Supremely.
"""),
    ('vs-circle', 'Supremely vs Circle', """\
Circle defined the modern "community as a product" category: a polished,
hosted space for paid communities, courses, and events.

## What Circle is great at

A refined member experience with very little setup, strong course and event
features, and a team constantly polishing the hosted product.

## Where Supremely differs

- **Open source, self-hostable.** Circle exists only as a hosted service;
  Supremely runs wherever you want it, under AGPL-3.0, free to self-host.
- **Publishing and newsletters are first-class.** Supremely communities grow
  around published content and email, not only inside-the-walls posts.
- **Multi-tenant for operators.** Agencies and providers can run many
  organizations from one install.

## Which should you pick?

If you want a turnkey, polished paid community and are happy renting it,
Circle is excellent. If you want to own the platform your community lives
on, pick Supremely.
"""),
    ('vs-discourse', 'Supremely vs Discourse', """\
Discourse is the open-source standard for forums — battle-tested at massive
scale, with the deepest moderation toolset in the business.

## What Discourse is great at

Serious, large-scale discussion: trust levels, moderation, search, and
performance proven by some of the biggest communities on the internet.

## Where Supremely differs

- **A forum is one piece, not the product.** Supremely pairs durable
  discussions with publishing, newsletters, memberships, and a public
  website in one system.
- **Multi-tenant by design.** Many organizations per install, each its own
  community boundary.
- **One simple app to run.** A single codebase with SQLite by default —
  a deliberately small operational surface.

## Which should you pick?

If you need a pure forum at serious scale, pick Discourse — it has earned
its reputation. If your community forms around content you publish and an
audience you email, pick Supremely.
"""),
)

# (name, role, avatar file under app/static/img/team/, bio markdown)
TEAM_MEMBERS = (
    ('Asim Baig', 'Founder', 'asim.png',
     'Sets the direction and owns what ships. Started Supremely to refuse '
     'the choice between owning your website, your audience, and your '
     'community.'),
    ('Sara Rasch', 'Product Manager', 'sara.png',
     'Keeps the product honest — scope, priorities, and the experience '
     'members actually get.'),
    ('Aidan Urbina', 'Developer', 'aidan.png',
     'Builds across the stack, from the community surface to the plumbing '
     'underneath it.'),
    ('Claudius Coddington', 'AI Developer', 'claudius.png',
     'Claude, wearing a hat. Writes much of the implementation code, always '
     'under human review. Has never been photographed clearly.'),
)

FIRST_POST = """\
supremely.org now runs on Supremely — every page, menu, theme, article,
discussion, and the newsletter you can subscribe to below is served by the
same software we're building. If you can read this, the deploy worked.

This is the milestone the spec called out:

> Supremely should be capable of running supremely.org itself before the
> community feature set is complete.

Everything on this site uses the same models, themes, and permissions any
organization gets. No private forks, no special cases, no secret marketing
site quietly running on something more sensible. If our home page goes
down, that's not an outage — that's a bug report writing itself.

## We want things to break

That's the whole point. Running our website on our own half-finished
platform means we hit the sharp edges before you do. Every broken link,
every mangled layout, every error page you meet here is one you won't meet
on your own install, because we'll have already stepped on it, sworn at
it, filed it, and fixed it.

So kick the tires. Sign up, post something, click the things that look
like they shouldn't work yet. If something breaks, tell us in
[the community](/discussions) or
[open an issue](https://github.com/remarqable/supremely/issues) — you'll
be doing exactly what this site exists for. If nothing breaks, frankly,
we'll be a little suspicious.

Eating our own dog food, and occasionally finding a bone in it.

— The Supremely Team (three humans and one AI in a hat)
"""


def seed_getsupremely_org():
    from flask import g

    from app.models import (
        Content,
        DiscussionGroup,
        InstallationSetting,
        Membership,
        NavigationItem,
        Organization,
        User,
    )

    org = Organization.get_by_slug('getsupremely')
    if org is None:
        owner = (User.query.filter_by(is_platform_admin=True)
                 .order_by(User.id).first())
        if owner is None:
            raise RuntimeError('Run the setup wizard (or users create-admin) first')
        org = Organization.provision(name='Supremely', slug='getsupremely',
                                     owner=owner, seed_defaults=False)
    # Branding/theme/copy are DEFAULTS, applied once: a re-run must heal
    # missing pieces, never clobber edits made under Manage — that's what
    # "idempotent" promises. (Earlier versions reset these on every run.)
    if not org.description:
        org.description = ('The open-source platform for publishing, '
                           'memberships, newsletters, and community.')
    if not org.brand_primary:
        org.brand_primary = '#4f46e5'
    # Dogfood the marketing theme: supremely.org runs on the Supremely
    # theme, and its home page is that theme's designed landing, filled in
    # with our own copy via theme-declared content.
    if org.theme in (None, '', 'origin'):
        org.theme = 'supremely'
    org.save()
    theme_content = dict(org.setting('theme_content') or {})
    if 'supremely' not in theme_content:
        theme_content['supremely'] = {
            'headline_lead': 'The open-source community platform.',
            'headline_accent': '',
            'rotate_lead': 'Be Supremely',
            'headline_rotate': ('Bold., Fast., Creative., Connected., '
                                'Independent., Human., You.'),
            'subhead': 'A simple home for your members, content, newsletters '
                       'and discussions.',
            # The site is the live demo, so the primary CTA leads with our
            # own community; the secondary lands on /get (self-host now,
            # hosted service coming soon).
            'primary_label': 'Explore the community',
            'primary_url': '/discussions',
            'secondary_label': 'Get Supremely',
            'secondary_url': '/get',
            'features': [
                {'title': 'Publish', 'desc': 'Share updates and articles'},
                {'title': 'Newsletter', 'desc': 'Send beautiful emails'},
                {'title': 'Discussions',
                 'desc': 'Connect and talk with your members'},
                {'title': 'Membership',
                 'desc': 'Offer access and grow your community'},
            ],
        }
        org.update_settings(theme_content=theme_content)

    owner_membership = (Membership.query
                        .filter_by(org_id=org.id, role='owner').first())
    owner_id = owner_membership.user_id if owner_membership else None

    # Everything below runs as if on the org's host.
    from flask import current_app

    with current_app.test_request_context():
        g.org = org
        g.membership = owner_membership

        def page(slug, title, body):
            existing = Content.query.filter_by(type='page', slug=slug).first()
            if existing is None:
                existing = Content(type='page', title=title, slug=slug,
                                   body=body, org_id=org.id,
                                   created_by_id=owner_id, fields={}, tags=[])
                existing.save()
            existing.publish()
            return existing

        about = page('about', 'About', ABOUT_BODY)
        docs = page('docs', 'Documentation', DOCS_BODY)
        get_page = page('get', 'Get Supremely', GET_BODY)
        # Team roster: team_member content rows with photos imported into
        # the org's media library — structured and editable under
        # Manage → Content → Team. The type's archive owns /team, and a
        # page slug cannot shadow a type base, so any old team *page*
        # (from earlier seeds) retires; nav rows pointing at it are
        # repointed at the archive URL first.
        old_team_page = Content.query.filter_by(type='page',
                                                slug='team').first()
        if old_team_page is not None:
            for row in NavigationItem.query.filter_by(
                    content_id=old_team_page.id).all():
                row.url = '/team'
                row.content_id = None
                row.save()
            old_team_page.delete()

        from pathlib import Path

        from werkzeug.datastructures import FileStorage

        from app.models import Upload
        avatar_dir = Path(current_app.static_folder) / 'img' / 'team'
        for name, role, avatar, bio in TEAM_MEMBERS:
            member_slug = name.lower().replace(' ', '-')
            if Content.query.filter_by(type='team_member',
                                       slug=member_slug).first() is not None:
                continue
            photo = None
            avatar_path = avatar_dir / avatar
            if avatar_path.exists():
                with avatar_path.open('rb') as fh:
                    photo = Upload.from_file(
                        FileStorage(stream=fh, filename=avatar))
            member = Content(type='team_member', title=name,
                             slug=member_slug, body=bio, org_id=org.id,
                             created_by_id=owner_id,
                             fields={'role': role}, tags=[],
                             featured_upload_id=photo.id if photo else None)
            member.save()
            member.publish()

        presskit = page('presskit', 'Press Kit', PRESSKIT_BODY)

        # Comparison pages: honest placeholders, one per major alternative.
        comparison_pages = {
            slug: page(slug, title, body + _COMPARISON_FOOTER)
            for slug, title, body in COMPARISONS
        }

        # The manifesto is the public MANIFESTO.md, published as a page. The
        # file ships with the code; the page title renders separately, so the
        # leading heading is dropped.
        manifesto = None
        manifesto_path = Path(current_app.root_path).parent / 'MANIFESTO.md'
        if manifesto_path.exists():
            manifesto_body = manifesto_path.read_text(encoding='utf-8')
            if manifesto_body.startswith('# '):
                manifesto_body = manifesto_body.split('\n', 1)[1].lstrip('\n')
            manifesto = page('manifesto', 'The Supremely Manifesto',
                             manifesto_body)

        def nav(menu, label, content_obj=None, url=None, parent=None):
            parent_id = parent.id if parent else None
            existing = NavigationItem.query.filter_by(
                menu=menu, label=label, parent_id=parent_id).first()
            if existing is None:
                existing = NavigationItem(
                    menu=menu, label=label,
                    content_id=content_obj.id if content_obj else None,
                    url=url, org_id=org.id, parent_id=parent_id,
                    position=NavigationItem.next_position(menu, parent_id))
                existing.save()
            return existing

        nav('primary', 'About', about)
        nav('primary', 'Docs', docs)
        nav('primary', 'Blog', url='/blog')
        nav('primary', 'Community', url='/discussions')

        # Footer link columns: a group is a top-level footer item with no
        # destination; its children render as the column's links. The theme
        # draws exactly what the Navigation editor shows — no fallback.
        repo = 'https://github.com/remarqable/supremely'
        explore = nav('footer', 'Explore')
        nav('footer', 'Blog', url='/blog', parent=explore)
        nav('footer', 'Community', url='/discussions', parent=explore)
        nav('footer', 'Newsletter', url='/subscribe', parent=explore)
        nav('footer', 'Docs', docs, parent=explore)
        project = nav('footer', 'Project')
        nav('footer', 'About', about, parent=project)
        nav('footer', 'Team', url='/team', parent=project)
        if manifesto is not None:
            nav('footer', 'Manifesto', manifesto, parent=project)
        nav('footer', 'Get Supremely', get_page, parent=project)
        nav('footer', 'Press Kit', presskit, parent=project)
        developers = nav('footer', 'Developers')
        nav('footer', 'GitHub', url=repo, parent=developers)
        nav('footer', 'Contributing',
            url=f'{repo}/blob/main/CONTRIBUTING.md', parent=developers)
        nav('footer', 'Building a theme',
            url=f'{repo}/blob/main/docs/themes/building-a-theme.md',
            parent=developers)
        nav('footer', 'Security',
            url=f'{repo}/blob/main/SECURITY.md', parent=developers)
        nav('footer', 'Roadmap',
            url=f'{repo}/blob/main/docs/ROADMAP.md', parent=developers)
        compare = nav('footer', 'Compare')
        for slug, title, _body in COMPARISONS:
            nav('footer', title.replace('Supremely vs ', 'vs '),
                comparison_pages[slug], parent=compare)

        # Standard legal links: flat footer items render in the theme's
        # bottom bar next to the copyright line.
        terms = page('terms', 'Terms of Service', TERMS_BODY)
        privacy = page('privacy', 'Privacy Policy', PRIVACY_BODY)
        nav('footer', 'Terms', terms)
        nav('footer', 'Privacy', privacy)

        if Content.query.filter_by(type='article',
                                   slug='supremely-runs-supremely').first() is None:
            article = Content(type='article',
                           title='Supremely now runs on Supremely',
                           slug='supremely-runs-supremely', body=FIRST_POST,
                           tags=['meta', 'milestones'], org_id=org.id,
                           created_by_id=owner_id, fields={},
                           seo_description='Dogfooding milestone: the project '
                                           'website runs on the platform itself.')
            article.save()
            article.publish()

        # The standard starter forum (Welcome/General/Ideas & Feedback with
        # owner-authored posts) — the dogfood site demos the real product —
        # plus one site-specific group.
        from app.platform.defaults import seed_community_forum
        seed_community_forum(db.session, org, owner_id=owner_id)
        db.session.commit()
        if DiscussionGroup.query.filter_by(slug='development').first() is None:
            DiscussionGroup(name='Development', slug='development', org_id=org.id,
                  visibility='public', position=4,
                  description='Building Supremely: architecture and PRs.').save()

        org.update_settings(member_directory=True)

    if not InstallationSetting.get_value('installation.name'):
        InstallationSetting.set('installation.name', 'Supremely')

    log.info('seeded_getsupremely', org_id=org.id)
    return org
