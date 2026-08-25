"""Dogfood seed: getsupremely.org built on Supremely itself.

Demonstrates pages, theme, navigation, posts, documentation content,
newsletter signup, community discussions, and membership -- entirely through
the same models any organization uses. Idempotent.
"""

from app.extensions import db
from app.platform.logger import get_logger

log = get_logger()

HOME_BODY = """\
# Publishing, memberships, and community — on your own terms

**Supremely** is an open-source, multi-tenant platform for publishing,
memberships, newsletters, and discussions. One codebase serves self-hosted
installations, third-party hosts, and the official hosted service.

- **Pages are core** — host a complete website, not a community bolted on
- **Email is optional** — install, publish, and onboard with no SMTP anywhere
- **Structured content** — verticals emerge through Post Types and plugins,
  never forks
- **Presentation is replaceable** — themes restyle everything, content
  survives

[Read the docs](/docs) · [Join the discussion](/discussions) ·
[Subscribe](/subscribe)
"""

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
4. Familiar ecosystem concepts: themes, templates, posts, plugins, hooks
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
| Post | The universal publishing unit; typed via Post Types |
| Space | A discussion area holding topics and replies |
| Theme | A per-organization presentation package |
| Plugin | Boot-loaded code, enabled per organization |

## Operating without email

Everything works with no SMTP: password auth, CLI recovery
(`flask users reset-password`), copyable invitation links, and web
publishing. Configure email later in Administration to unlock invitations
by mail, notification copies, and newsletters.
"""

FIRST_POST = """\
Supremely now runs its own website — pages, navigation, theming, posts,
discussions, and the newsletter you can subscribe to below.

This is the milestone the spec called out:

> Supremely should be capable of running getsupremely.org itself before the
> community feature set is complete.

Everything on this site uses the same models, themes, and permissions any
organization gets. No private forks, no special cases.
"""


def seed_getsupremely_org():
    from flask import g
    from app.models import (InstallationSetting, Membership, NavigationItem,
                            Organization, Page, Post, Space, User)

    org = Organization.get_by_slug('getsupremely')
    if org is None:
        owner = (User.query.filter_by(is_platform_admin=True)
                 .order_by(User.id).first())
        if owner is None:
            raise RuntimeError('Run the setup wizard (or users create-admin) first')
        org = Organization.provision(name='Supremely', slug='getsupremely',
                                     owner=owner, seed_defaults=False)
    org.description = ('The open-source platform for publishing, memberships, '
                       'newsletters, and community.')
    org.brand_primary = '#4f46e5'
    org.save()

    owner_membership = (Membership.query
                        .filter_by(org_id=org.id, role='owner').first())
    owner_id = owner_membership.user_id if owner_membership else None

    # Everything below runs as if on the org's host.
    from app import create_app  # noqa: F401  (context helper below)
    from flask import current_app
    with current_app.test_request_context():
        g.org = org
        g.membership = owner_membership

        def page(slug, title, body, template='page', homepage=False):
            existing = Page.query.filter_by(slug=slug).first()
            if existing is None:
                existing = Page(title=title, slug=slug, body=body,
                                template=template, org_id=org.id,
                                created_by_id=owner_id)
                existing.save()
            existing.publish()
            if homepage:
                org.update_settings(homepage_page_id=existing.id)
            return existing

        home = page('welcome', 'Supremely', HOME_BODY, homepage=True)
        about = page('about', 'About', ABOUT_BODY)
        docs = page('docs', 'Documentation', DOCS_BODY)

        def nav(menu, label, page_obj=None, url=None):
            existing = NavigationItem.query.filter_by(menu=menu,
                                                      label=label).first()
            if existing is None:
                NavigationItem(menu=menu, label=label,
                               page_id=page_obj.id if page_obj else None,
                               url=url, org_id=org.id,
                               position=NavigationItem.next_position(menu)
                               ).save()

        nav('primary', 'About', about)
        nav('primary', 'Docs', docs)
        nav('primary', 'Posts', url='/posts')
        nav('primary', 'Community', url='/discussions')
        nav('footer', 'Subscribe', url='/subscribe')
        nav('footer', 'Source', url='https://github.com/remarqable/supremely')

        if Post.query.filter_by(slug='supremely-runs-supremely').first() is None:
            post = Post(title='Supremely now runs on Supremely',
                        slug='supremely-runs-supremely', body=FIRST_POST,
                        tags=['meta', 'milestones'], org_id=org.id,
                        created_by_id=owner_id,
                        seo_description='Dogfooding milestone: the project '
                                        'website runs on the platform itself.')
            post.save()
            post.publish()

        if Space.query.filter_by(slug='general').first() is None:
            Space(name='General', slug='general', org_id=org.id,
                  visibility='public',
                  description='Questions, ideas, and show-and-tell.').save()
        if Space.query.filter_by(slug='development').first() is None:
            Space(name='Development', slug='development', org_id=org.id,
                  visibility='public',
                  description='Building Supremely: architecture and PRs.').save()

        org.update_settings(member_directory=True)

    if not InstallationSetting.get_value('installation.name'):
        InstallationSetting.set('installation.name', 'Supremely')

    log.info('seeded_getsupremely', org_id=org.id)
    return org
