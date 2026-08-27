"""`flask seed demo`: a ready-to-click demo community for local testing.

Wipes-then-runs via `make demo`. Seeds a VANILLA community through the
standard provisioning path — exactly what a new customer org gets — then
fills the showroom: named members, forum replies and reactions, and one
published item per library content type, so every sidebar section, the
feed, and the widgets look real.

Fixed, guessable credentials (everything is `password`), so this refuses
to run in production.
"""

from flask import current_app, g

from app.extensions import db
from app.platform.logger import get_logger

log = get_logger()

ADMIN_EMAIL = 'admin@demo.test'
PASSWORD = 'password'

MEMBERS = (
    ('maya@demo.test', 'Maya Chen',
     'Product designer. Sketching community spaces by day.'),
    ('omar@demo.test', 'Omar Haddad',
     'Backend tinkerer, coffee over-thinker.'),
    ('priya@demo.test', 'Priya Patel',
     'Writer and workshop host. Ask me about zines.'),
    ('leo@demo.test', 'Leo Fischer',
     'Photographer, mostly bikes and bread.'),
)


def seed_demo() -> dict:
    from app.models import Membership, Organization, User
    from app.platform.config_store import mark_installed

    if current_app.config.get('APP_ENV') == 'production':
        raise RuntimeError('seed demo uses fixed credentials and refuses '
                           'to run in production.')
    if Organization.get_by_slug('demo') is not None:
        raise RuntimeError("The 'demo' community already exists — "
                           "run `make demo` for a fresh one.")

    admin = User.get_by_email(ADMIN_EMAIL) or User.create(
        email=ADMIN_EMAIL, name='Admin', password=PASSWORD,
        is_platform_admin=True)
    mark_installed(current_app)

    org = Organization.provision(name='Demo Community', slug='demo',
                                 owner=admin)

    members = []
    for email, name, bio in MEMBERS:
        user = User.create(email=email, name=name, password=PASSWORD)
        user.bio = bio
        user.save()
        Membership.add(user.id, org.id, role='member')
        members.append(user)

    with current_app.test_request_context():
        g.org = org
        _fill_forum(org, members)
        _fill_library(org, admin)

    log.info('seeded_demo', org_id=org.id)
    return {'org': org, 'admin': admin, 'members': members}


def _fill_forum(org, members) -> None:
    """Replies, reactions, and one member post on the seeded starter forum."""
    from app.models.discussion import Post, Reaction, Reply

    maya, omar, priya, leo = members

    def reply(post, author, body, parent=None):
        row = Reply(org_id=org.id, post_id=post.id, body=body,
                    parent_id=parent.id if parent else None,
                    created_by_id=author.id)
        db.session.add(row)
        db.session.flush()
        return row

    intro = Post.query.filter_by(title='Introduce yourself').first()
    if intro is not None:
        first = reply(intro, maya, "Hi everyone! I'm Maya — designer, "
                                   'here to trade notes on building spaces '
                                   'people actually come back to.')
        reply(intro, omar, 'Omar here. Backend person, happy to help with '
                           'anything self-hosting.')
        reply(intro, priya, 'Welcome Maya! Love that framing.', parent=first)
        intro.recount_replies()
        intro.touch()

    working = Post.query.filter_by(title="What's everyone working on?").first()
    if working is not None:
        reply(working, leo, 'Photographing a bakery series this month — '
                            'will share contact sheets when the light '
                            'cooperates.')
        working.recount_replies()
        working.touch()

    general_post = Post(org_id=org.id,
                        group_id=working.group_id if working else intro.group_id,
                        title="What I'm building this month",
                        body='A tiny hardware synth. Weekly progress in '
                             'this thread — replies and hot takes welcome.',
                        created_by_id=maya.id)
    db.session.add(general_post)
    db.session.flush()

    welcome = Post.query.filter_by(is_seeded=True).first()
    db.session.commit()
    if welcome is not None:
        Reaction.toggle(maya.id, 'post', welcome.id, '👍')
        Reaction.toggle(omar.id, 'post', welcome.id, '👍')
        Reaction.toggle(priya.id, 'post', welcome.id, '❤️')
    if intro is not None and intro.replies:
        Reaction.toggle(leo.id, 'reply', intro.replies[0].id, '👍')
    db.session.commit()


def _fill_library(org, admin) -> None:
    """One published item per library content type, so Learn isn't empty."""
    from app.models import Content
    from app.models.base import utcnow

    now = utcnow()
    items = (
        ('recording', 'Community kickoff — recording', 'kickoff-recording',
         'The full recording of our first community call.',
         {'video_url': 'https://example.com/videos/kickoff',
          'duration_minutes': 42, 'speakers': 'Admin, Maya Chen',
          'recorded_on': now.strftime('%Y-%m-%d')}),
        ('episode', 'Episode 1: Why this community exists', 'episode-1',
         'The origin story, and where we want to take this.',
         {'audio_url': 'https://example.com/audio/episode-1',
          'episode_number': 1, 'duration_minutes': 28}),
        ('resource', 'Community handbook', 'community-handbook',
         'Everything new members need in one place.',
         {'resource_url': 'https://example.com/files/handbook.pdf',
          'kind': 'Guide'}),
    )
    for type_slug, title, slug, body, fields in items:
        db.session.add(Content(org_id=org.id, type=type_slug, title=title,
                               slug=slug, body=body, fields=fields, tags=[],
                               status='published', published_at=now,
                               visibility='public', created_by_id=admin.id))
    db.session.commit()
