"""Starter content for new organizations.

Every fresh organization gets a small, working website: a designated
homepage, an About page, primary/footer navigation, a first post, and a
General discussion space. Owners edit or delete all of it under Manage.

Works with any SQLAlchemy session (the wizard's PostgreSQL path seeds a
session that is not db.session), so objects are constructed with known-good
constants and added directly -- no .save()/validate round-trips.
"""

HOME_BODY = """\
# Welcome to {name}

This is your new home on the web — publish pages and posts, grow a
newsletter audience, and host discussions, all in one place.

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
    from app.models.discussion import Space
    from app.models.navigation import NavigationItem
    from app.models.page import Page
    from app.models.post import Post

    now = utcnow()

    home = Page(org_id=org.id, title='Home', slug='home',
                body=HOME_BODY.format(name=org.name),
                status='published', published_at=now, visibility='public',
                template='page', created_by_id=owner_id,
                seo_description=f'{org.name} — publishing, newsletter, and community.')
    about = Page(org_id=org.id, title='About', slug='about',
                 body=ABOUT_BODY.format(name=org.name),
                 status='published', published_at=now, visibility='public',
                 template='page', created_by_id=owner_id)
    session.add_all([home, about])
    session.flush()                     # need page ids for nav + homepage

    org.settings = {**(org.settings or {}), 'homepage_page_id': home.id}

    session.add_all([
        NavigationItem(org_id=org.id, menu='primary', label='Home',
                       url='/', position=1),
        NavigationItem(org_id=org.id, menu='primary', label='About',
                       page_id=about.id, position=2),
        NavigationItem(org_id=org.id, menu='primary', label='Posts',
                       url='/posts', position=3),
        NavigationItem(org_id=org.id, menu='primary', label='Discussions',
                       url='/discussions', position=4),
        NavigationItem(org_id=org.id, menu='footer', label='Subscribe',
                       url='/subscribe', position=1),
    ])

    session.add(Post(org_id=org.id, type='article', title='Hello, World!',
                     slug='hello-world',
                     body=FIRST_POST_BODY.format(name=org.name),
                     excerpt=f'The first post on {org.name}.',
                     status='published', published_at=now,
                     visibility='public', tags=['welcome'], fields={},
                     created_by_id=owner_id))

    session.add(Space(org_id=org.id, name='General', slug='general',
                      visibility='members', position=1,
                      description='Introductions, questions, and everything else.'))
