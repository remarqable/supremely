"""Dogfood seed: getsupremely.org built on Supremely itself.

Demonstrates pages, theme, navigation, articles, documentation content,
newsletter signup, community discussions, and membership -- entirely through
the same models any organization uses. Idempotent.
"""

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

FIRST_POST = """\
Supremely now runs its own website — pages, navigation, theming, articles,
discussions, and the newsletter you can subscribe to below.

This is the milestone the spec called out:

> Supremely should be capable of running getsupremely.org itself before the
> community feature set is complete.

Everything on this site uses the same models, themes, and permissions any
organization gets. No private forks, no special cases.
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
    org.description = ('The open-source platform for publishing, memberships, '
                       'newsletters, and community.')
    org.brand_primary = '#4f46e5'
    # Dogfood the marketing theme: getsupremely.org runs on the Supremely
    # theme, and its home page is that theme's designed landing, filled in
    # with our own copy via theme-declared content.
    org.theme = 'supremely'
    org.save()
    org.update_settings(theme_content={'supremely': {
        'headline_lead': 'The open-source',
        'headline_accent': 'community platform.',
        'subhead': 'A simple home for your members, content, newsletters '
                   'and discussions.',
        'primary_label': 'Get Started',
        'secondary_label': 'View Demo',
        'secondary_url': '/discussions',
        'features': [
            {'title': 'Publish', 'desc': 'Share updates and articles'},
            {'title': 'Newsletter', 'desc': 'Send beautiful emails'},
            {'title': 'Discussions', 'desc': 'Connect and talk with your members'},
            {'title': 'Membership', 'desc': 'Offer access and grow your community'},
        ],
    }})

    owner_membership = (Membership.query
                        .filter_by(org_id=org.id, role='owner').first())
    owner_id = owner_membership.user_id if owner_membership else None

    # Everything below runs as if on the org's host.
    from flask import current_app

    from app import create_app  # noqa: F401  (context helper below)
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

        def nav(menu, label, content_obj=None, url=None):
            existing = NavigationItem.query.filter_by(menu=menu,
                                                      label=label).first()
            if existing is None:
                NavigationItem(menu=menu, label=label,
                               content_id=content_obj.id if content_obj else None,
                               url=url, org_id=org.id,
                               position=NavigationItem.next_position(menu)
                               ).save()

        nav('primary', 'About', about)
        nav('primary', 'Docs', docs)
        nav('primary', 'Blog', url='/blog')
        nav('primary', 'Community', url='/discussions')
        nav('footer', 'Subscribe', url='/subscribe')
        nav('footer', 'Source', url='https://github.com/remarqable/supremely')

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

        if DiscussionGroup.query.filter_by(slug='general').first() is None:
            DiscussionGroup(name='General', slug='general', org_id=org.id,
                  visibility='public',
                  description='Questions, ideas, and show-and-tell.').save()
        if DiscussionGroup.query.filter_by(slug='development').first() is None:
            DiscussionGroup(name='Development', slug='development', org_id=org.id,
                  visibility='public',
                  description='Building Supremely: architecture and PRs.').save()

        org.update_settings(member_directory=True)

    if not InstallationSetting.get_value('installation.name'):
        InstallationSetting.set('installation.name', 'Supremely')

    log.info('seeded_getsupremely', org_id=org.id)
    return org
