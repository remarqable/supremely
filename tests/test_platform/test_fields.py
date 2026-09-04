"""Rendering the fields a content type declares.

The fields were always collected and stored; nothing put them on a screen.
What did reach a reader got there through slug tests in templates, which is
the callsite membership test the architecture forbids. These tests pin the
replacement: one renderer, chosen by field type and key, overridable by a
theme, on every surface a reader sees.
"""

import re
from pathlib import Path

import pytest
from flask import g

from app.models import Content
from app.platform.content_types import ContentType, FieldSpec
from app.platform.fields import (
    render_field,
    render_fields,
    render_fields_text,
    render_lead_field,
    safe_url,
    video_embed,
)
from tests.test_platform.test_theme_contract import install

ACME = 'http://acme.example.test'


def publish(org, title, type_slug, fields, **kwargs):
    item = Content(type=type_slug, title=title,
                   slug=title.lower().replace(' ', '-'),
                   body=f'Body of {title}', org_id=org.id,
                   fields=fields, tags=[], **kwargs)
    item.save()
    item.publish()
    return item


# --- what reaches the reader --------------------------------------------------

def test_a_video_url_renders_as_a_player(app, client, acme, globex, user):
    """Issue #83: the field was stored, validated, and displayed nowhere.

    Asserted on the community page, the themed page and the newsletter,
    because "renders" that is true on one surface and false on the other two
    is how this got missed in the first place.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'Deep dive', 'recording',
                       {'video_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'})
        item_id = item.id

    page = client.get('/recordings/deep-dive', base_url=ACME).data
    assert b'<iframe' in page
    assert b'youtube-nocookie.com/embed/dQw4w9WgXcQ' in page

    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.extensions import db
        from app.platform.newsletter import compose_email
        stored = db.session.get(Content, item_id)
        subscriber = type('S', (), {'email': 'r@example.com', 'token': 't'})()
        _, text, html = compose_email(stored, acme, subscriber)
    # No iframe in email: no client renders one. A link is actionable in all.
    assert 'youtube.com/watch?v=dQw4w9WgXcQ' in html
    assert '<iframe' not in html
    assert 'Video URL: https://www.youtube.com/watch?v=dQw4w9WgXcQ' in text


def test_an_audio_url_and_a_resource_url_render_too(app, client, acme, globex,
                                                    user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Episode one', 'episode',
                {'audio_url': 'https://cdn.example.com/ep1.mp3'})
        publish(acme, 'The report', 'resource',
                {'resource_url': 'https://cdn.example.com/report.pdf',
                 'kind': 'Whitepaper'})

    episode = client.get('/podcast/episode-one', base_url=ACME).data
    assert b'<audio' in episode
    assert b'cdn.example.com/ep1.mp3' in episode

    resource = client.get('/resources/the-report', base_url=ACME).data
    assert b'cdn.example.com/report.pdf' in resource
    assert b'Whitepaper' in resource        # the second, plain field renders


def test_a_field_with_no_value_renders_nothing(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'No kind', 'resource',
                       {'resource_url': 'https://cdn.example.com/x.pdf'})
        html = render_fields(item)
    assert 'x.pdf' in html
    assert 'Kind' not in html


def test_fields_render_in_declaration_order_not_storage_order(app, acme):
    """Two items of one type should read the same way whichever order the
    editor happened to write their JSON in."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'Backwards', 'resource',
                       {'kind': 'Guide',
                        'resource_url': 'https://cdn.example.com/g.pdf'})
        html = str(render_fields(item))
    assert html.index('g.pdf') < html.index('Guide')


# --- the embed, which is the security-carrying part ---------------------------

@pytest.mark.parametrize('url,expected', [
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ',
     'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'),
    ('https://youtu.be/dQw4w9WgXcQ',
     'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'),
    ('https://www.youtube.com/embed/dQw4w9WgXcQ',
     'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'),
    ('https://vimeo.com/123456789', 'https://player.vimeo.com/video/123456789'),
    ('https://vimeo.com/video/123456789',
     'https://player.vimeo.com/video/123456789'),
])
def test_known_hosts_become_an_embed_url(url, expected):
    assert video_embed(url) == expected


@pytest.mark.parametrize('url', [
    'https://example.com/video.mp4',                    # not a known host
    'https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ',    # host suffix
    'https://evil.test/#youtu.be/dQw4w9WgXcQ',              # in the fragment
    'https://evil.test/?u=https://youtu.be/dQw4w9WgXcQ',    # in a parameter
    'https://evil.test/youtube.com/watch?v=dQw4w9WgXcQ',    # in the path
    'javascript:alert(1)//youtu.be/dQw4w9WgXcQ',            # not even a link
    '', None, 12345,
])
def test_only_a_real_known_host_embeds(url):
    """Every one of these carries a known host's text somewhere, and none of
    them is that host. Matching the text rather than the parsed host puts
    somebody else's page inside an iframe on ours."""
    assert video_embed(url) is None


@pytest.mark.parametrize('url', [
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    'https://m.youtube.com/watch?v=dQw4w9WgXcQ',
    'https://www.youtube.com/shorts/dQw4w9WgXcQ',
    'https://vimeo.com/123456789',
])
def test_an_embed_url_is_built_from_an_id_never_from_the_url(url):
    """What is emitted is assembled from a parsed id, so no part of the
    author's URL survives into the iframe src."""
    assert re.fullmatch(
        r'https://(www\.youtube-nocookie\.com/embed/[A-Za-z0-9_-]{11}'
        r'|player\.vimeo\.com/video/\d{6,12})', video_embed(url))


def test_an_unknown_host_falls_back_to_a_link_not_a_broken_frame(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'Self hosted', 'recording',
                       {'video_url': 'https://example.com/talk.mp4'})
        html = str(render_fields(item))
    assert '<iframe' not in html
    assert 'https://example.com/talk.mp4' in html


# --- resolution order ---------------------------------------------------------

def test_a_theme_overrides_a_field_type(app, client, acme, globex):
    """The contract: drop in fields/url.html and every URL field changes."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Episode two', 'episode',
                {'audio_url': 'https://cdn.example.com/ep2.mp3'})
    install(app, acme, **{
        'fields__url.html': '<p class="themed">{{ value }}</p>',
        'single.html': ("{% extends site_layout %}{% block content %}"
                        "{{ render_fields(content) }}{% endblock %}"),
    })
    body = client.get('/podcast/episode-two', base_url=ACME).data
    assert b'class="themed"' in body
    assert b'<audio' not in body        # the core key partial lost to the theme


def test_a_key_beats_its_type_at_every_level(app, client, acme, globex):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Episode three', 'episode',
                {'audio_url': 'https://cdn.example.com/ep3.mp3'})
    install(app, acme, **{
        'fields__url.html': '<p class="by-type">{{ value }}</p>',
        'fields__url-audio_url.html': '<p class="by-key">{{ value }}</p>',
        'single.html': ("{% extends site_layout %}{% block content %}"
                        "{{ render_fields(content) }}{% endblock %}"),
    })
    body = client.get('/podcast/episode-three', base_url=ACME).data
    assert b'class="by-key"' in body
    assert b'class="by-type"' not in body


def test_a_themes_default_adds_a_fallback_it_does_not_remove_the_embeds(
        app, client, acme, globex):
    """A theme shipping fields/_default.html is saying "when nothing of mine
    matched", not "replace the video player with a line of text".

    Ordered per level, the theme's default came before core's
    url-video_url.html and silently killed the embed. Defaults now sort
    after every typed partial anywhere.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Still embedded', 'recording',
                {'video_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'})
        publish(acme, 'Plain kind', 'resource',
                {'resource_url': 'https://cdn.example.com/k.pdf',
                 'kind': 'Guide'})
    install(app, acme, **{
        'fields___default.html': '<p class="mine">{{ value }}</p>',
        'single.html': ("{% extends site_layout %}{% block content %}"
                        "{{ render_fields(content) }}{% endblock %}"),
    })

    video = client.get('/recordings/still-embedded', base_url=ACME).data
    assert b'youtube-nocookie.com/embed/' in video

    # And it is a fallback where nothing typed matched: 'kind' is a string,
    # which no partial claims, so the theme's default draws it.
    resource = client.get('/resources/plain-kind', base_url=ACME).data
    assert b'class="mine"' in resource
    assert b'Guide' in resource


def test_a_field_type_nobody_drew_still_shows_its_value(app, acme):
    """A plugin or a theme may declare a field type this installation has no
    partial for. The family's _default draws it, so the value reaches the
    reader instead of disappearing, which is the whole complaint this stage
    exists to answer."""
    spec = FieldSpec(key='mystery', type='quantum', label='Mystery')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        for surface in ('web', 'summary', 'email'):
            html = str(render_field(spec, 'a value', surface=surface))
            assert 'a value' in html, surface


def test_a_lead_slot_has_no_default_so_a_card_falls_back_to_the_avatar(app,
                                                                       acme):
    """The one family without a default, deliberately. A type leading with a
    field nothing draws should lead with nothing at all; a bare value in the
    slot where a date block goes would look like a mistake."""
    spec = FieldSpec(key='mystery', type='quantum', label='Mystery')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert str(render_field(spec, 'a value', surface='lead')) == ''


def test_a_summary_never_borrows_a_page_row(app, acme):
    """A card is an inline strip. Falling back to the page's labelled row
    dropped a block element into the middle of one, so the summary family
    stops at its own default rather than reaching for the web partials."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        for field_type in ('string', 'text', 'boolean', 'number', 'quantum'):
            spec = FieldSpec(key='k', type=field_type, label='L',
                             in_summary=True)
            html = str(render_field(spec, 'x' if field_type != 'boolean' else True,
                                    surface='summary'))
            assert 'field_row' not in html, field_type
            assert 'mt-3 flex gap-2' not in html, field_type


# --- surfaces -----------------------------------------------------------------

def test_email_never_borrows_the_web_partials(app, acme):
    """No stylesheet reaches an email client, so the email family carries
    inline styles and must not fall back to the class-based web ones."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'Styled', 'resource',
                       {'resource_url': 'https://cdn.example.com/s.pdf',
                        'kind': 'Guide'})
        html = str(render_fields(item, surface='email'))
    assert 'style=' in html
    assert 'class=' not in html
    assert '<tr>' in html


def test_a_summary_renders_only_the_fields_marked_for_it(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'A talk', 'resource',
                       {'resource_url': 'https://cdn.example.com/t.pdf',
                        'kind': 'Guide'})
        # resource marks nothing in_summary, so a card shows none of it.
        assert str(render_fields(item, surface='summary')) == ''

        event = publish(acme, 'Kickoff', 'event',
                        {'starts_on': '2026-05-24', 'location': 'Berlin'})
        summary = str(render_fields(event, surface='summary'))
    # The lead field is the card's leading block already; saying it twice is
    # the obvious way to get this wrong.
    assert 'Berlin' in summary
    assert '2026' not in summary


def test_the_lead_field_is_what_the_type_names(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        event = publish(acme, 'Kickoff two', 'event',
                        {'starts_on': '2026-05-24', 'location': 'Berlin'})
        assert 'MAY' in str(render_lead_field(event))

        # A type that names no lead field gets nothing, and the caller falls
        # back to whatever a card normally shows.
        article = publish(acme, 'Plain', 'article', {})
        assert str(render_lead_field(article)) == ''


def test_a_named_lead_field_with_no_value_leads_with_nothing(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        event = publish(acme, 'Undated', 'event', {'location': 'Berlin'})
        assert str(render_lead_field(event)) == ''


def test_a_repeating_field_reads_as_rows_in_a_text_email(app, acme):
    """One line per row, not per value. Split by sub-field it reads

        - 2
        - onions

    which is an amount and a thing on separate lines, neither meaning
    anything on its own.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        recipe = publish(acme, 'Soup', 'recipe', {
            'servings': 4,
            'ingredients': [{'amount': '2', 'item': 'onions'},
                            {'amount': '1 tsp', 'item': 'salt'}]})
        text = render_fields_text(recipe)
    assert '  - 2 onions' in text
    assert '  - 1 tsp salt' in text
    assert "{'amount'" not in text          # never a Python repr at a reader


def test_a_select_and_a_picture_read_as_words_in_a_text_email(app, acme):
    """A text email has no partial to draw it, so the formatting has to
    happen anyway: raw, a select prints its stored key and an image prints
    a row id."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        job = publish(acme, 'Cook', 'job', {
            'employment': 'full_time',
            'apply_url': 'https://example.com/a'})
        text = render_fields_text(job)
    assert 'Full time' in text
    assert 'full_time' not in text


def test_text_email_carries_the_fields_too(app, acme):
    """Some clients show the text half. A podcast email without the episode
    link is a title and a paragraph."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'Episode four', 'episode',
                       {'audio_url': 'https://cdn.example.com/ep4.mp3'})
        text = render_fields_text(item)
    assert 'Audio URL: https://cdn.example.com/ep4.mp3' in text


def test_fields_render_with_no_request_at_all(app, acme):
    """A newsletter is a job, and a job has no request.

    Rendering through flask.render_template ran the application's context
    processors, which evaluate is_mobile() and so read the request. The
    email surface could not render a single field, and the failure surfaced
    as one dead recipient at a time in the delivery log.
    """
    from app.extensions import db

    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'Episode six', 'episode',
                       {'audio_url': 'https://cdn.example.com/ep6.mp3'})
        item_id = item.id

    with app.app_context():         # no request context, as the worker has
        stored = db.session.get(Content, item_id)
        html = str(render_fields(stored, surface='email'))
    assert 'cdn.example.com/ep6.mp3' in html


def test_a_partial_that_calls_itself_stops(app, client, acme, globex):
    """A list draws its rows through render_field, and a theme's own partial
    can call it too. The model cannot express a field inside a field inside
    a field, so anything that deep is a partial calling itself."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Loop', 'resource',
                {'resource_url': 'https://cdn.example.com/l.pdf'})
    install(app, acme, **{
        'fields__url.html': "{{ render_field(spec, value, 'web') }}",
        'single.html': ("{% extends site_layout %}{% block content %}"
                        "{{ render_fields(content) }}{% endblock %}"),
    })
    # Renders at all rather than recursing until the stack gives out.
    assert client.get('/resources/loop', base_url=ACME).status_code == 200


def test_an_upload_id_too_long_to_parse_is_refused(app, acme):
    """isdigit() is happy with five thousand digits and int() is not, so the
    guard has to bound the length as well as the alphabet."""
    from app.platform.content_types import FieldSpec as Spec
    from app.platform.errors import ValidationError as Invalid
    with app.test_request_context(base_url=ACME):
        g.org = acme
        spec = Spec(key='photo', type='image', label='Photo')
        for refused in ('9' * 5000, '9' * 20, '-1', '1.5', 'null'):
            with pytest.raises(Invalid):
                spec.clean(refused)
        # An optional picture left unchosen is not an error, it is no value.
        assert spec.clean('') is None


def test_an_unknown_surface_is_refused(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        with pytest.raises(ValueError):
            render_field(FieldSpec(key='x'), 'v', surface='billboard')


# --- the rule this stage exists to enforce ------------------------------------

def test_no_template_decides_anything_by_content_type_slug():
    """The acceptance criterion for the stage.

    Greps every shape of the test, not the one string this change deleted:
    the first version of this looked for `content_type.slug ==` alone and
    passed while the sidebar still branched on `slug ==` through five types
    to pick an icon. A type's slug is an identifier, not a switch.

    The one match allowed is manage/theme.html, where `slug` is a *theme*
    slug being compared with the active theme, not a content type at all.
    """
    views = Path(__file__).resolve().parents[2] / 'app' / 'views'
    pattern = re.compile(r'(content_type|ct|item|content)?\.?\bslug\b\s*==')
    offenders = sorted(
        str(path.relative_to(views))
        for path in views.rglob('*.html')
        if pattern.search(path.read_text())
    )
    assert offenders == ['manage/theme.html'], offenders


def test_a_url_that_is_not_a_link_never_becomes_one(app, acme):
    """FieldSpec.clean refuses a non-http URL on the way in, but a stored
    value has not always been through it: set_structured_fields keeps values
    under keys the type did not declare at the time, and seeds build rows
    directly. Escaping quotes a javascript: URL, it does not disarm it, so
    the check is repeated where the value becomes an href.
    """
    for scheme in ('javascript:alert(1)', 'data:text/html,<b>x</b>',
                   'vbscript:msgbox(1)', ' javascript:alert(1)'):
        assert safe_url(scheme) == ''
    assert safe_url('https://example.com/a.pdf') == 'https://example.com/a.pdf'

    # The property is that it never lands in an attribute a browser follows.
    # Rendering it as escaped text is fine and is what the partials do, so
    # the author can see what they typed.
    attribute = re.compile(r'(href|src)\s*=\s*["\']?\s*javascript:', re.I)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        for key in ('resource_url', 'audio_url', 'video_url'):
            for surface in ('web', 'email'):
                spec = FieldSpec(key=key, type='url', label='L')
                html = str(render_field(spec, 'javascript:alert(1)',
                                        surface=surface))
                assert not attribute.search(html), (key, surface)
                assert '<iframe' not in html, (key, surface)
                assert '<audio' not in html, (key, surface)


def test_the_policy_allows_exactly_the_hosts_the_embed_builds(app):
    """A player that renders in the markup and nowhere else is not a
    feature. The iframe and the audio element both inherit default-src
    'self' unless the policy names them, so the policy and the renderer are
    checked against each other here.
    """
    from app.platform.content import VIDEO_FRAME_HOSTS
    with app.test_client() as client:
        policy = client.get('/health').headers['Content-Security-Policy']
    assert 'frame-src' in policy
    assert 'media-src' in policy
    for host in VIDEO_FRAME_HOSTS:
        assert host in policy
    embedded = video_embed('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
    assert any(embedded.startswith(host) for host in VIDEO_FRAME_HOSTS)


def test_one_bad_value_costs_its_own_field_and_nothing_else(app, acme):
    """JSON has no schema and types change, so a value can be the wrong
    shape for the type that now declares it. Inside a newsletter this sits
    in the per-recipient try, where one raised exception marked every
    recipient failed.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'Wrong shape', 'event',
                       {'starts_on': 20260524, 'location': 'Berlin'})
        html = str(render_fields(item))
        assert 'Berlin' in html          # the good field still renders
        assert str(render_lead_field(item)) == ''


def test_every_listing_surface_shows_the_summary_fields(app, client, acme,
                                                        globex, user):
    """Community archive, themed archive and the upcoming-event rail all
    list items, so all three show what the type marked for listings. The
    themed archive is where a site-presented type's listing lives, and it
    was showing none of them."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Field day', 'event',
                {'starts_on': '2026-05-24', 'location': 'Berlin'})

    community = client.get('/events', base_url=ACME).data
    assert b'Berlin' in community
    assert b'MAY' in community                  # the lead block

    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models import Content
        from app.platform.theming import render_site
        items = Content.published_query('event').all()
        themed = render_site(['archive-event.html', 'archive.html'],
                             force_theme=True, items=items,
                             content_type=items[0].content_type,
                             page=1, pages=1)
    assert 'Berlin' in themed


def test_the_upcoming_event_rail_leads_with_the_shared_date_block(app, client,
                                                                  acme, globex,
                                                                  user):
    """The rail card leads with the same block the archive card does, from
    the same partial: this markup used to be written out twice. Asserted on
    the block the fixture's own upcoming event produces, since that is the
    one the rail picks.

    The separator matters here. It belonged to the archive's meta strip, and
    a chip carrying its own left it dangling in front of the first one.
    """
    from tests.conftest import login_as
    login_as(client, user)
    rail = client.get('/dashboard', base_url=ACME).get_data(as_text=True)
    assert 'Kickoff meetup' in rail             # the fixture's event
    # The lead partial's own markup, so the rail is provably using it.
    assert 'bg-red-500' in rail
    assert 'tabular-nums' in rail
    assert '>· ' not in rail


def test_a_type_cannot_lead_with_a_field_it_does_not_have():
    with pytest.raises(ValueError, match='lead_field'):
        ContentType(slug='broken', singular='B', plural='Bs', base='/bs',
                    lead_field='nope',
                    fields=(FieldSpec(key='real'),)).validate_definition()
