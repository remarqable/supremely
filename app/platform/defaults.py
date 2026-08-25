"""Starter content for new organizations.

Every fresh organization gets a small, working website modeled on a real
community-product split:

- External pages: Home (designated homepage), About, FAQ, Contact
- Primary navigation with dropdown groups: Community, Resources, About
- A grouped footer mirroring the navigation
- A first post, a General discussion space, and the member directory on

Owners edit or delete all of it under Manage.

Works with any SQLAlchemy session (the wizard's PostgreSQL path seeds a
session that is not db.session), so objects are constructed with known-good
constants and added directly -- no .save()/validate round-trips.
"""

HOME_BODY = """\
# Welcome to {name}

A community for people who care about what we're building — publish, discuss,
and grow together.

- Read the latest on the [blog](/posts)
- Join the conversation in [discussions](/discussions)
- Get new posts by email — [subscribe](/subscribe)

*You're looking at the starter homepage. Owners can edit or replace it
under **Manage → Pages**.*
"""

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

### How do I get new posts?

[Subscribe](/subscribe) to receive new posts by email, or follow the
[blog](/posts).
"""

CONTACT_BODY = """\
Want to reach the team behind **{name}**?

Edit this page under **Manage → Pages** and add your preferred contact
channels — an email address, a form link, or your social profiles.
"""

FIRST_POST_BODY = """\
Hello, world! This is the first post on **{name}**.

Posts support **Markdown**, categories, tags, featured images, and
member-only visibility. This one was created automatically — edit or delete
it under **Manage → Posts**, then write something real.

Happy publishing!
"""


def seed_default_content(session, org, owner_id=None) -> None:
    """Idempotent-enough for fresh orgs: only ever called at provisioning."""
    from app.models.base import utcnow
    from app.models.content import Content
    from app.models.discussion import Space
    from app.models.navigation import NavigationItem

    now = utcnow()

    def page(slug, title, body, seo=None):
        return Content(org_id=org.id, type='page', title=title, slug=slug,
                       body=body, status='published', published_at=now,
                       visibility='public', created_by_id=owner_id,
                       seo_description=seo, fields={}, tags=[])

    home = page('home', 'Home', HOME_BODY.format(name=org.name),
                seo=f'{org.name} — publishing, newsletter, and community.')
    about = page('about', 'About', ABOUT_BODY.format(name=org.name))
    faq = page('faq', 'FAQ', FAQ_BODY.format(name=org.name))
    contact = page('contact', 'Contact', CONTACT_BODY.format(name=org.name))
    session.add_all([home, about, faq, contact])
    session.flush()                     # need ids for nav + homepage

    org.settings = {**(org.settings or {}),
                    'homepage_content_id': home.id,
                    'member_directory': True}

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

    # A first article, and one Event so the vertical content type is a visible
    # hint (this is where custom content goes).
    session.add(Content(org_id=org.id, type='article', title='Hello, World!',
                        slug='hello-world',
                        body=FIRST_POST_BODY.format(name=org.name),
                        excerpt=f'The first post on {org.name}.',
                        status='published', published_at=now,
                        visibility='public', tags=['welcome'], fields={},
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

    session.add(Space(org_id=org.id, name='General', slug='general',
                      visibility='members', position=1,
                      description='Introductions, questions, and everything else.'))
