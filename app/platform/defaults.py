"""Starter content for new organizations.

Every fresh organization gets a small, working website modeled on a real
community-product split:

- Home page: the active theme's front page (Origin's hero), editable under
  Manage → Home page — not a CMS page that can shadow it
- Pages: About, FAQ, Contact
- Primary navigation with dropdown groups: Community, Resources, About
- A grouped footer mirroring the navigation
- A first article, a General discussion group, and the member directory on

Owners edit or delete all of it under Manage.

Works with any SQLAlchemy session (the wizard's PostgreSQL path seeds a
session that is not db.session), so objects are constructed with known-good
constants and added directly -- no .save()/validate round-trips.
"""

# The default (Origin) home page is a hero; seed friendly copy for it so a
# fresh site isn't bare. The headline falls back to the org name.
HOME_SUBHEAD = ("A community for people who care about what we're building — "
                "publish, discuss, and grow together.")

ABOUT_BODY = """\
Tell the world what **{name}** is about.

This page is a placeholder — open **Manage → Pages** and make it yours:
who you are, what you publish, and why people should stick around.
"""

FAQ_BODY = """\
### What is {name}?

A community and publication. Replace this answer with your own.

### How do I become a member?

Members join by invitation. Ask an organizer for an invite link — it works
even before email is configured.

### How do I get updates?

[Subscribe](/subscribe) to receive updates by email, or follow the
[blog](/blog).
"""

CONTACT_BODY = """\
Want to reach the team behind **{name}**?

Edit this page under **Manage → Pages** and add your preferred contact
channels — an email address, a form link, or your social profiles.
"""

FIRST_ANNOUNCEMENT_BODY = """\
Official updates from the team live here and in the community sidebar, so
members see them without going looking. Edit or delete this one under
**Manage → Announcements**, then tell your members what's new.
"""

FIRST_ARTICLE_BODY = """\
The first article on **{name}**, and a demonstration of what one can hold.

Articles support **Markdown**, categories, tags, featured images, and
member-only visibility. This one was created automatically — edit or delete
it under **Manage → Blog**, then write something real.

Happy publishing!
"""

MEMBERS_ARTICLE_BODY = """\
If you can read this, you're a member — welcome!

This article is published with **member-only visibility**: visitors see its
title in the blog with a lock and land on a friendly members-only page if
they click it, while members read it in full. That mix of public and gated
content is how {name} shows visitors what membership unlocks.

Set visibility per article, page, or discussion group. Edit or delete this
one under **Manage → Blog**.
"""


def seed_default_content(session, org, owner_id=None,
                         vertical=None) -> None:
    """Idempotent-enough for fresh orgs: only ever called at provisioning."""
    from app.models.base import utcnow
    from app.models.content import Content
    from app.models.navigation import NavigationItem

    now = utcnow()

    def page(slug, title, body, seo=None):
        return Content(org_id=org.id, type='page', title=title, slug=slug,
                       body=body, status='published', published_at=now,
                       visibility='public', created_by_id=owner_id,
                       seo_description=seo, fields={}, tags=[])

    about = page('about', 'About', ABOUT_BODY.format(name=org.name))
    faq = page('faq', 'FAQ', FAQ_BODY.format(name=org.name))
    contact = page('contact', 'Contact', CONTACT_BODY.format(name=org.name))
    session.add_all([about, faq, contact])
    session.flush()                     # need ids for nav links

    org.settings = {**(org.settings or {}),
                    'member_directory': True,
                    # Friendly starter copy for Origin's home-page hero
                    # (Manage → Home page). Headline defaults to the org name.
                    'theme_content': {'origin': {
                        'subhead': HOME_SUBHEAD,
                        'cta_label': 'Subscribe', 'cta_url': '/subscribe'}}}

    def nav(menu, label, position, url=None, content_id=None, parent=None):
        item = NavigationItem(org_id=org.id, menu=menu, label=label, url=url,
                              content_id=content_id, position=position,
                              parent_id=parent.id if parent else None)
        session.add(item)
        return item

    # Primary navigation: flat links + dropdown groups
    nav('primary', 'Home', 1, url='/')
    community = nav('primary', 'Community', 2)
    resources = nav('primary', 'Resources', 3)
    about_group = nav('primary', 'About', 4)
    session.flush()                     # group ids for children
    nav('primary', 'Discussions', 1, url='/discussions', parent=community)
    nav('primary', 'Members', 2, url='/members', parent=community)
    nav('primary', 'Blog', 1, url='/blog', parent=resources)
    nav('primary', 'Subscribe', 2, url='/subscribe', parent=resources)
    nav('primary', 'About', 1, content_id=about.id, parent=about_group)
    nav('primary', 'FAQ', 2, content_id=faq.id, parent=about_group)
    nav('primary', 'Contact', 3, content_id=contact.id, parent=about_group)

    # Footer: grouped columns mirroring the primary navigation
    f_community = nav('footer', 'Community', 1)
    f_resources = nav('footer', 'Resources', 2)
    f_about = nav('footer', 'About', 3)
    session.flush()
    nav('footer', 'Discussions', 1, url='/discussions', parent=f_community)
    nav('footer', 'Members', 2, url='/members', parent=f_community)
    nav('footer', 'Blog', 1, url='/blog', parent=f_resources)
    nav('footer', 'Subscribe', 2, url='/subscribe', parent=f_resources)
    nav('footer', 'About', 1, content_id=about.id, parent=f_about)
    nav('footer', 'FAQ', 2, content_id=faq.id, parent=f_about)
    nav('footer', 'Contact', 3, content_id=contact.id, parent=f_about)

    # A first article, an Event, and a welcome Announcement so the typed
    # content surfaces are visible hints (this is where custom content goes).
    # Library-type examples so Learn (recordings, podcast, resources) and
    # the Publish menu demonstrate themselves on day one. Owner-authored,
    # ordinary content — edit or delete under Manage.
    session.add(Content(org_id=org.id, type='recording',
                        title='Example: a community video',
                        slug='example-video',
                        body='A placeholder video so you can see how video '
                             'content renders. Replace it under '
                             '**Manage → Videos**.',
                        status='published', published_at=now,
                        visibility='public', tags=[], created_by_id=owner_id,
                        fields={'video_url': 'https://example.com/videos/hello'}))
    session.add(Content(org_id=org.id, type='episode',
                        title='Example: a podcast episode',
                        slug='example-episode',
                        body='A placeholder episode showing how podcast '
                             'content renders. Replace it under '
                             '**Manage → Podcast**.',
                        status='published', published_at=now,
                        visibility='public', tags=[], created_by_id=owner_id,
                        fields={'audio_url': 'https://example.com/audio/hello'}))
    session.add(Content(org_id=org.id, type='resource',
                        title='Example: a shared resource',
                        slug='example-resource',
                        body='A placeholder document link showing how '
                             'resources render. Replace it under '
                             '**Manage → Resources**.',
                        status='published', published_at=now,
                        visibility='public', tags=[], created_by_id=owner_id,
                        fields={'resource_url': 'https://example.com/files/guide.pdf',
                                'kind': 'Guide'}))
    session.add(Content(org_id=org.id, type='announcement',
                        title=f'Welcome to {org.name}',
                        slug='welcome',
                        body=FIRST_ANNOUNCEMENT_BODY.format(name=org.name),
                        status='published', published_at=now,
                        visibility='public', tags=[], fields={},
                        created_by_id=owner_id))
    session.add(Content(org_id=org.id, type='article', title='Hello, World!',
                        slug='hello-world',
                        body=FIRST_ARTICLE_BODY.format(name=org.name),
                        status='published', published_at=now,
                        visibility='public', tags=['welcome'], fields={},
                        created_by_id=owner_id))
    # One gated article beside the public one: together they demonstrate
    # tease-don't-hide (locked title in the blog, gate page on click).
    session.add(Content(org_id=org.id, type='article',
                        title='For members: how gated content works',
                        slug='for-members',
                        body=MEMBERS_ARTICLE_BODY.format(name=org.name),
                        status='published', published_at=now,
                        visibility='members', tags=['welcome'], fields={},
                        created_by_id=owner_id))
    session.add(Content(org_id=org.id, type='event', title='Kickoff meetup',
                        slug='kickoff-meetup',
                        body='Our first community event. Edit or delete this '
                             'under Manage → Events.',
                        status='published', published_at=now,
                        visibility='public', tags=[],
                        fields={'starts_on': now.strftime('%Y-%m-%d'),
                                'location': 'Online'},
                        created_by_id=owner_id))

    seed_community_forum(session, org, owner_id=owner_id, vertical=vertical)


def seed_community_forum(session, org, owner_id=None, vertical=None) -> None:
    """The starter forum: three groups, six owner-authored posts — enough to
    demonstrate the model without feeling padded. Structure comes from the
    community_seed resolver so vertical overlays can reshape it. Groups that
    already exist are skipped untouched (re-seeding heals, never duplicates
    or overwrites)."""
    from app.models.discussion import DiscussionGroup, Post
    from app.platform.community_seed import resolve_seed

    # Provisioning runs with no tenant resolved, so the global filter does not
    # apply and this filter is what keeps the read to the org being seeded.
    existing = {group.slug for group in
                DiscussionGroup.query.filter_by(org_id=org.id).all()}
    for group_spec in resolve_seed(vertical)['groups']:
        if group_spec['slug'] in existing:
            continue                             # heal, never touch or dupe
        group = DiscussionGroup(org_id=org.id, name=group_spec['name'],
                                slug=group_spec['slug'],
                                visibility=group_spec['visibility'],
                                position=group_spec['position'])
        session.add(group)
        session.flush()                          # need group.id for posts
        for post_spec in group_spec['posts']:
            session.add(Post(org_id=org.id, group_id=group.id,
                             title=post_spec['title'],
                             body=post_spec['body'],
                             is_pinned=bool(post_spec.get('pinned')),
                             is_seeded=bool(post_spec.get('seeded')),
                             created_by_id=owner_id))
