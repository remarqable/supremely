"""Body directives: :::embed and :::feed.

An author references other content from inside a body. The two things that
matter here are the order sanitizing and resolution happen in, and that a
directive can never show somebody content they could not have reached by
asking for it directly.
"""

from flask import g

from app.models import Content
from app.platform.content import render_markdown
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def publish(app, org, **kwargs):
    defaults = {'type': 'article', 'title': 'Host', 'slug': 'host',
                'body': 'Body.', 'org_id': org.id, 'fields': {}, 'tags': [],
                'visibility': 'public'}
    defaults.update(kwargs)
    with app.test_request_context(base_url=ACME):
        g.org = org
        item = Content(**defaults)
        item.save()
        item.publish()
        return item


def rendered(app, org, body):
    with app.test_request_context(base_url=ACME):
        g.org = org
        return render_markdown(body)


def episode(app, org, title='Why We Build', slug='why-we-build',
            body='Episode notes.', visibility='public'):
    org.set_type_settings('episode', enabled=True)
    return publish(app, org, type='episode', title=title, slug=slug,
                   body=body, visibility=visibility,
                   fields={'audio_url': 'https://cdn.example.com/e.mp3'})


# --- the security property -----------------------------------------------------

def test_the_author_is_sanitized_before_anything_is_spliced_in(app, acme):
    """The order is the whole argument, so it is asserted directly.

    Sanitize the author's markdown, then splice trusted partial output into
    the cleaned document. Reversed, the directive's own markup would be
    stripped by the cleaner, and -- far worse -- the author's raw HTML would
    be sitting in the document at the moment the splice made it trusted.
    """
    episode(app, acme)
    out = rendered(app, acme,
                   '<script>alert(1)</script>\n\n'
                   '<img src=x onerror="alert(2)">\n\n'
                   '<figure class="pretending">mine</figure>\n\n'
                   ':::embed episode/why-we-build')
    assert '<script>' not in out
    assert 'onerror' not in out
    assert 'alert(' not in out
    assert 'pretending' not in out            # the author's class, stripped
    # ...and the splice's own class survived. That is the proof of ordering
    # and not merely of rendering: the cleaner strips class from a figure,
    # so a figure that still carries one was added after the cleaner ran.
    assert 'Why We Build' in out
    assert '<figure class=' in out


def test_a_directive_written_by_an_author_is_not_a_tag(app, acme):
    """The directive is matched as text in the cleaned document. An author
    who types markup that looks like the splice output gets text, because
    their markup went through the cleaner like everything else."""
    out = rendered(app, acme, '<aside class="evil">x</aside>\n\n'
                              'plain :::embed episode/why-we-build inline')
    assert 'class="evil"' not in out
    # Mid-sentence is not a directive: it is the words somebody typed.
    assert ':::embed' in out


# --- what an embed shows -------------------------------------------------------

def test_an_embed_renders_the_item_with_its_fields(app, acme):
    episode(app, acme)
    out = rendered(app, acme, 'Before.\n\n:::embed episode/why-we-build\n\nAfter.')
    assert 'Before.' in out and 'After.' in out      # the prose survives
    assert 'Why We Build' in out
    assert 'Episode notes.' in out
    assert 'cdn.example.com/e.mp3' in out            # the type's own fields
    assert '/podcast/why-we-build' in out            # links to the real item
    assert ':::embed' not in out


def test_an_embed_of_a_gated_item_never_shows_its_body(app, client, acme, user):
    """A members-only target is a locked teaser or nothing, per the tease
    setting. An embed must not become a way to lift gated words into a
    public article."""
    episode(app, acme, title='Members Only', slug='members-only',
            body='Paid words.', visibility='members')
    host = publish(app, acme, body=':::embed episode/members-only')

    acme.update_settings(gated_teasers=True)
    teased = client.get('/blog/host', base_url=ACME).get_data(as_text=True)
    assert 'Members Only' in teased                  # the title, locked
    assert 'Paid words.' not in teased

    acme.update_settings(gated_teasers=False)
    hidden = client.get('/blog/host', base_url=ACME).get_data(as_text=True)
    assert 'Members Only' not in hidden
    assert 'Paid words.' not in hidden

    login_as(client, user)
    for teasing in (True, False):
        acme.update_settings(gated_teasers=teasing)
        member = client.get('/blog/host', base_url=ACME).get_data(as_text=True)
        assert 'Paid words.' in member, teasing
    assert host


def test_an_embed_of_a_locked_section_shows_nothing(app, acme):
    """The per-type lock gates an embed the way it gates the archive."""
    episode(app, acme, title='Locked Section', slug='locked-section')
    acme.set_type_settings('episode', visibility='members')
    out = rendered(app, acme, ':::embed episode/locked-section')
    assert 'Locked Section' not in out


def test_an_embed_of_a_type_the_org_turned_off_shows_nothing(app, acme):
    episode(app, acme, title='Turned Off', slug='turned-off')
    acme.set_type_settings('episode', enabled=False)
    out = rendered(app, acme, ':::embed episode/turned-off')
    assert 'Turned Off' not in out


def test_a_draft_or_missing_target_renders_nothing(app, acme):
    """Directives are typed by hand, so a typo leaves a gap rather than an
    error in the middle of somebody's article."""
    acme.set_type_settings('episode', enabled=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        draft = Content(type='episode', title='Still A Draft', slug='draft-one',
                        body='Unpublished.', org_id=acme.id, fields={}, tags=[],
                        visibility='public')
        draft.save()                                  # never published

    # A directive that parses but points at nothing: the paragraph goes.
    for body in (':::embed episode/no-such-slug',
                 ':::embed episode/draft-one',
                 ':::embed nosuchtype/anything'):
        out = rendered(app, acme, f'A.\n\n{body}\n\nB.')
        assert 'Still A Draft' not in out, body
        assert 'Unpublished.' not in out, body
        assert body not in out, body                  # no directive text left
        assert 'A.' in out and 'B.' in out, body      # the prose is untouched


def test_a_directive_that_cannot_be_honoured_leaves_nothing(app, acme):
    """No error, no placeholder, whatever went wrong.

    A typo in the target, a missing slash, no argument at all: none of them
    resolve, and none of them leave `:::embed ...` sitting in a published
    page. Raw directive text is machinery showing through, and the spec
    rules it out for the same reason it rules out an error message.
    """
    acme.set_type_settings('episode', enabled=True)
    for body in (':::embed is how you reference another post.',
                 ':::embed episode why-we-build',
                 ':::embed ../../etc/passwd',
                 ':::embed not-a-target',
                 ':::embed',
                 ':::feed'):
        out = rendered(app, acme, f'A.\n\n{body}\n\nB.')
        assert 'A.' in out and 'B.' in out, body      # the prose is untouched
        assert '<figure' not in out, body             # nothing was pulled in
        assert ':::' not in out, body                 # and nothing was left
        assert 'passwd' not in out, body


def test_an_author_writing_about_directives_uses_backticks(app, acme):
    """The escape hatch, and the reason nothing else needs one.

    A directive is a paragraph that is nothing but a directive, so code
    spans and code blocks are already inert -- which is where anyone
    documenting the syntax would put it anyway.
    """
    episode(app, acme, title='Real Episode', slug='real-episode')
    out = rendered(app, acme,
                   'Write `:::embed episode/real-episode` to pull one in.\n\n'
                   '    :::embed episode/real-episode\n')
    assert out.count(':::embed episode/real-episode') == 2
    assert 'Real Episode' not in out
    assert '<figure' not in out


def test_a_target_with_anything_after_it_is_not_a_directive(app, acme):
    """The pattern is anchored end to end. Without that, a paragraph that
    began with a valid target would embed it and swallow the rest of the
    sentence."""
    episode(app, acme, title='Real Episode', slug='real-episode')
    out = rendered(app, acme,
                   'A.\n\n:::embed episode/real-episode and then some words\n\nB.')
    assert 'Real Episode' not in out          # nothing embedded
    assert 'A.' in out and 'B.' in out


def test_a_block_cannot_be_embedded(app, acme):
    """A block has no address, so it cannot be named by one. published_by_slug
    only ever answers with standalone rows."""
    acme.set_type_settings('recipe', enabled=True)
    host = publish(app, acme, slug='host-article')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        card = Content(type='recipe', title='Inline Card', body='Mix it.',
                       org_id=acme.id, fields={}, tags=[], visibility='public',
                       parent_id=host.id)
        card.save()
        card.publish()
    out = rendered(app, acme, ':::embed recipe/inline-card')
    assert 'Inline Card' not in out


# --- the recursion guard -------------------------------------------------------

def test_an_embed_loop_terminates(app, acme):
    """Two articles embedding each other, and one embedding itself. An
    embedded item renders its body without resolving further directives, so
    there is nothing to descend into."""
    acme.set_type_settings('episode', enabled=True)
    first = publish(app, acme, title='First', slug='first',
                    body='First body.\n\n:::embed article/second')
    publish(app, acme, title='Second', slug='second',
            body='Second body.\n\n:::embed article/first')
    publish(app, acme, title='Selfish', slug='selfish',
            body='Mine.\n\n:::embed article/selfish')

    with app.test_request_context(base_url=ACME):
        g.org = acme
        out = first.html
    assert 'First body.' in out
    assert 'Second body.' in out               # one level down
    # ...and the directive inside the embedded item is left as text rather
    # than followed, so nothing recurses.
    assert out.count('First body.') == 1

    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert 'Mine.' in Content.published_by_slug('article', 'selfish').html


def test_an_embedded_body_has_its_own_directives_removed(app, acme):
    """html_flat is what the embed partial renders and what a summary reads,
    and this is the property it is for.

    Removed rather than left as text: a card inside a card must not show the
    words ":::embed article/second", and neither should a one-line archive
    summary. Asserted on its own rather than through an embed, because the
    renderer has a second guard and a test going through one would pass with
    this one gone.
    """
    first = publish(app, acme, title='First', slug='first',
                    body='First body.\n\n:::embed article/second')
    publish(app, acme, title='Second', slug='second', body='Second body.')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        flat = first.html_flat
    assert 'First body.' in flat
    assert ':::' not in flat                     # removed, not resolved
    assert 'Second body.' not in flat


def test_a_summary_is_the_items_own_words(app, acme):
    """Every archive card and front-page tile calls this. Reading the
    resolved body pulled whatever the item embeds into the parent's
    summary, and charged a query and a template render per directive for
    text that is then stripped of all its markup anyway."""
    episode(app, acme, title='Target', slug='target', body='TARGET BODY.')
    host = publish(app, acme, slug='host',
                   body='Intro words.\n\n:::embed episode/target')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        summary = host.excerpt_or_summary(200)
    assert 'Intro words.' in summary
    assert 'TARGET BODY.' not in summary
    assert ':::' not in summary


def test_the_depth_cap_does_not_depend_on_the_template_asking_nicely(app, acme):
    """The embed partial is theme-overridable, so the cap cannot rest on a
    template naming the right property. A theme rendering the target's
    `html` instead of its `html_flat` would follow a body that references
    itself until the worker died; the renderer refuses regardless.

    Asserted on whether an embed was rendered at all, not on whether the
    directive text survives: the text survives either way, because a
    resolved embed draws a body that still contains it.
    """
    publish(app, acme, title='Selfish', slug='selfish',
            body='Mine.\n\n:::embed article/selfish')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content.published_by_slug('article', 'selfish')
        # Exactly what an unwary theme does: ask for `html` from inside a
        # resolution that is already running.
        from app.platform import content as content_module
        content_module._resolving.active = True
        try:
            out = item.html
        finally:
            content_module._resolving.active = False
    assert 'Mine.' in out
    assert ':::' not in out            # the directive is gone
    assert '<figure' not in out        # and nothing was pulled in


# --- the feed directive --------------------------------------------------------

def test_a_feed_directive_renders_the_types_latest_items(app, acme):
    """The same partial the front-page window draws: one data verb, two
    callers. A section an author places in a body and one a theme places on
    the front page are the same thing."""
    episode(app, acme, title='Episode One', slug='episode-one')
    episode(app, acme, title='Episode Two', slug='episode-two')
    out = rendered(app, acme, 'Intro.\n\n:::feed episode limit=3')
    assert 'Intro.' in out
    assert 'Episode One' in out and 'Episode Two' in out
    assert '/podcast/episode-one' in out       # links into the community
    assert ':::feed' not in out


def test_a_feed_directive_honours_its_limit(app, acme):
    for n in range(4):
        episode(app, acme, title=f'Episode {n}', slug=f'episode-{n}')
    out = rendered(app, acme, ':::feed episode limit=1')
    assert sum(f'Episode {n}' in out for n in range(4)) == 1


def test_a_feed_directive_shows_no_gated_body(app, acme):
    episode(app, acme, title='Members Episode', slug='members-episode',
            body='Paid words.', visibility='members')
    out = rendered(app, acme, ':::feed episode limit=5')
    assert 'Paid words.' not in out


def test_a_feed_of_a_type_the_org_turned_off_renders_nothing(app, acme):
    episode(app, acme, title='Turned Off', slug='turned-off')
    acme.set_type_settings('episode', enabled=False)
    out = rendered(app, acme, 'A.\n\n:::feed episode\n\nB.')
    assert 'Turned Off' not in out
    assert 'A.' in out and 'B.' in out


# --- outside a request ---------------------------------------------------------

def test_a_newsletter_sends_the_prose_and_not_the_directive(app, acme):
    """A newsletter is a job: there is nobody to ask who is reading, so
    there is no safe answer. The directives resolve to nothing rather than
    to somebody else's view of the site, and the raw text never ships."""
    episode(app, acme)
    with app.app_context():
        out = render_markdown('Before.\n\n:::embed episode/why-we-build\n\nAfter.')
    assert 'Before.' in out and 'After.' in out
    assert ':::embed' not in out
    assert 'Why We Build' not in out


# --- who may write a directive -------------------------------------------------

def test_a_discussion_post_never_resolves_a_directive(app, acme, user):
    """The renderer is shared with discussion posts and replies, which any
    member writes. A directive costs a query, an authorization check and a
    template render on the server; that belongs to somebody publishing the
    site, not to everyone who can start a thread.
    """
    from app.models.discussion import DiscussionGroup, Post, Reply
    episode(app, acme, title='Episode One', slug='episode-one')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = (DiscussionGroup.query.first()
                 or DiscussionGroup(name='Chat', slug='chat', org_id=acme.id))
        if group.id is None:
            group.save()
        post = Post(title='Look', body='Hi.\n\n:::embed episode/episode-one',
                    group_id=group.id, org_id=acme.id, created_by_id=user.id)
        post.save()
        reply = Reply(body=':::feed episode limit=3', post_id=post.id,
                      org_id=acme.id, created_by_id=user.id)
        reply.save()

        for rendered_body in (post.html, reply.html):
            assert 'Episode One' not in rendered_body
            assert '<figure' not in rendered_body
            assert ':::' in rendered_body        # left as the text it is


# --- cost ----------------------------------------------------------------------

def test_a_body_cannot_be_made_expensive_to_render(app, acme):
    """Two ways a body could cost the server more than it should.

    A pattern that backtracked turned 3,200 spaces into 22 seconds of CPU,
    and an uncapped directive count turned one saved body into thousands of
    queries. Both are bounded now, and both are asserted here rather than
    left to a reviewer to notice again.
    """
    import time
    episode(app, acme, title='Episode One', slug='episode-one')

    started = time.perf_counter()
    rendered(app, acme, ':::embed ' + ' ' * 4000 + '*x*')
    assert time.perf_counter() - started < 1.0

    body = '\n\n'.join([':::embed episode/episode-one'] * 40)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        import sqlalchemy as sa
        counted = {'n': 0}

        @sa.event.listens_for(sa.engine.Engine, 'before_cursor_execute')
        def before(conn, cursor, statement, params, context, many):
            counted['n'] += 1

        out = render_markdown(body)
        sa.event.remove(sa.engine.Engine, 'before_cursor_execute', before)
    assert out.count('<figure') == 10         # the cap, exactly
    assert counted['n'] < 40, counted         # nowhere near one query each
    # Past the cap they render as nothing, like any other directive that
    # cannot be honoured. Never the raw text.
    assert ':::' not in out


# --- tenancy -------------------------------------------------------------------

def test_a_directive_cannot_name_another_organizations_content(app, acme,
                                                               globex):
    """Slugs come out of a body somebody typed, so this resolves untrusted
    text into rows. Two organizations exist in the fixtures precisely so
    this is provable rather than assumed."""
    globex.set_type_settings('episode', enabled=True)
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        secret = Content(type='episode', title='Globex Secret',
                         slug='globex-secret', body='Their words.',
                         org_id=globex.id, fields={}, tags=[],
                         visibility='public')
        secret.save()
        secret.publish()

    acme.set_type_settings('episode', enabled=True)
    out = rendered(app, acme, ':::embed episode/globex-secret')
    assert 'Globex Secret' not in out
    assert 'Their words.' not in out

    # ...and the feed of a type both organizations publish shows only mine.
    episode(app, acme, title='Acme Episode', slug='acme-episode')
    feed = rendered(app, acme, ':::feed episode limit=10')
    assert 'Acme Episode' in feed
    assert 'Globex Secret' not in feed


# --- outside a request ---------------------------------------------------------

def test_nothing_is_resolved_without_a_reader_to_answer_for(app, acme,
                                                            monkeypatch):
    """Asserted on the guard and not only on the output.

    The output alone proved nothing: an error inside resolution was caught
    and turned into the same empty string the guard produces, so deleting
    the guard changed nothing a test could see.
    """
    from app.platform import content as content_module
    episode(app, acme)

    # Recorded rather than raised: an exception here is caught by the
    # renderer's own handler and turned into the same empty string the guard
    # produces, so raising would prove nothing.
    ran = []

    def record(*args, **kwargs):
        ran.append(1)
        return ''

    monkeypatch.setattr(content_module, '_render_embed', record)
    monkeypatch.setattr(content_module, '_render_feed', record)
    with app.app_context():
        out = render_markdown('Before.\n\n:::embed episode/why-we-build\n\nAfter.')
    assert ran == []                      # the guard, not the fallback
    assert 'Before.' in out and 'After.' in out
    assert ':::embed' not in out


def test_every_kind_of_reader_gets_the_right_half_of_a_gated_embed(
        app, client, acme, user, platform_admin):
    """Anonymous, plain member, org owner, platform admin.

    The owner is the fixture's `user`, so a suite that only tested `user`
    would never have distinguished a member from an admin, and the rule
    being tested is precisely about who is which.
    """
    episode(app, acme, title='Members Only', slug='members-only',
            body='Paid words.', visibility='members')
    publish(app, acme, body=':::embed episode/members-only')
    acme.update_settings(gated_teasers=True)

    def body_for(principal=None):
        fresh = app.test_client()
        if principal is not None:
            login_as(fresh, principal)
        return fresh.get('/blog/host', base_url=ACME).get_data(as_text=True)

    from app.models import Membership
    member = make_user(email='plain@example.com', name='Plain')
    Membership.add(member.id, acme.id, role='member')

    anonymous = body_for()
    assert 'Members Only' in anonymous and 'Paid words.' not in anonymous

    for principal in (member, user, platform_admin):
        seen = body_for(principal)
        assert 'Paid words.' in seen, principal.email


def test_one_type_can_be_teased_while_another_is_not(app, acme):
    """The per-type switch, not only the organization-wide one. A community
    may advertise its articles and say nothing at all about its jobs."""
    episode(app, acme, title='Members Episode', slug='members-episode',
            body='Paid words.', visibility='members')
    acme.update_settings(gated_teasers=True)

    acme.set_type_settings('episode', tease=True)
    teased = rendered(app, acme, ':::embed episode/members-episode')
    assert 'Members Episode' in teased and 'Paid words.' not in teased

    acme.set_type_settings('episode', tease=False)
    hidden = rendered(app, acme, ':::embed episode/members-episode')
    assert 'Members Episode' not in hidden


def test_a_directive_takes_the_slug_or_the_address_the_author_can_see(app,
                                                                     acme):
    """Both spellings find the same item.

    Not one type in the library has a URL base equal to its slug: article
    publishes at /blog, episode at /podcast, team_member at /team. An author
    writes what the address bar shows them, so accepting only the slug means
    the obvious spelling renders nothing, on every type, forever.
    """
    episode(app, acme, title='Why We Build', slug='why-we-build')
    by_slug = rendered(app, acme, ':::embed episode/why-we-build')
    by_base = rendered(app, acme, ':::embed podcast/why-we-build')
    assert 'Why We Build' in by_slug
    assert 'Why We Build' in by_base

    # The same for a feed, and for a base that is just the plural.
    acme.set_type_settings('resource', enabled=True)
    publish(app, acme, type='resource', title='Setup Guide',
            slug='setup-guide', body='How to.')
    assert 'Setup Guide' in rendered(app, acme, ':::feed resources limit=3')
    assert 'Setup Guide' in rendered(app, acme, ':::feed resource limit=3')


def test_a_type_slug_wins_over_another_types_address(app, acme):
    """Adding a type must never change what a body already points at, so a
    name that could be read either way is read as the slug."""
    from app.platform.content import _active_type
    from app.platform.content_types import active_types
    with app.test_request_context(base_url=ACME):
        g.org = acme
        for slug, content_type in active_types().items():
            assert _active_type(slug) is content_type, slug


def test_an_embed_is_a_card_and_not_prose(app, acme):
    """An embed lands inside the body's prose container, so the prose rules
    reach into it: the title becomes an underlined link and a button's label
    turns brand colour on a brand background, which reads as an empty box.

    The card carries a class the stylesheet uses to put it back, so the
    class is part of the contract rather than decoration somebody can drop.
    """
    episode(app, acme)
    out = rendered(app, acme, ':::embed episode/why-we-build')
    assert 'embed-card' in out
