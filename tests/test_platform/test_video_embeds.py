"""The :::video directive.

`iframe` is not in the Markdown allowlist, because one renderer serves both
editorial content and member-written discussion posts. A video is therefore a
directive: the author names a video, the renderer builds the frame.
"""
import pytest

from app.platform.content import VIDEO_FRAME_HOSTS, render_markdown

YOUTUBE = 'https://www.youtube.com/watch?v=QMH4rPEJ5BI'


@pytest.mark.parametrize('url', [
    YOUTUBE,
    'https://youtu.be/QMH4rPEJ5BI',
    'https://m.youtube.com/watch?v=QMH4rPEJ5BI',
    'https://www.youtube.com/embed/QMH4rPEJ5BI',
    'https://www.youtube.com/shorts/QMH4rPEJ5BI',
    'https://www.youtube.com/watch?list=PL1&v=QMH4rPEJ5BI',
])
def test_youtube_forms_all_embed_the_same_video(url):
    html = render_markdown(f':::video {url}')
    assert 'https://www.youtube-nocookie.com/embed/QMH4rPEJ5BI' in html
    assert '<iframe' in html


def test_vimeo_embeds():
    html = render_markdown(':::video https://vimeo.com/123456789')
    assert 'https://player.vimeo.com/video/123456789' in html


def test_the_frame_sits_beside_the_prose_not_inside_a_paragraph():
    html = render_markdown(f'Before.\n\n:::video {YOUTUBE}\n\nAfter.')
    assert '<p>Before.</p>' in html and '<p>After.</p>' in html
    assert '<p><div class="video-embed">' not in html


@pytest.mark.parametrize('url', [
    'https://evil.example.com/QMH4rPEJ5BI',
    'javascript:alert(1)',
    'https://www.youtube.com.evil.test/watch?v=QMH4rPEJ5BI',
    'https://vimeo.com/notanid',
    "https://www.youtube.com/watch?v=\" onload=\"alert(1)",
])
def test_anything_that_is_not_a_known_video_stays_plain_text(url):
    html = render_markdown(f':::video {url}')
    assert '<iframe' not in html
    assert 'youtube-nocookie' not in html


def test_a_raw_iframe_is_still_stripped():
    """The directive is the only way to a frame; the tag itself stays banned,
    because a member writing a discussion post runs through this too."""
    html = render_markdown('<iframe src="https://evil.example.com"></iframe>')
    assert '<iframe' not in html


def test_an_author_cannot_forge_the_internal_marker():
    """The marker carries a per-render random token, so text that looks like
    one is not swapped for a frame."""
    html = render_markdown('supremelyvideodeadbeefdeadbeef0')
    assert '<iframe' not in html
    assert 'supremelyvideodeadbeefdeadbeef0' in html


def test_the_directive_must_be_on_a_line_of_its_own():
    html = render_markdown(f'see :::video {YOUTUBE} inline')
    assert '<iframe' not in html


def test_email_rendering_gives_a_link_instead_of_a_frame():
    html = render_markdown(f':::video {YOUTUBE}', embed_videos=False)
    assert '<iframe' not in html
    assert f'href="{YOUTUBE}"' in html


def test_csp_frame_src_matches_the_hosts_the_renderer_emits(app, client, acme):
    """Both halves are required: nh3 lets the frame through, the browser has
    to be told the host is allowed. This is the check that they agree."""
    response = client.get('/', base_url='http://acme.example.test')
    csp = response.headers['Content-Security-Policy']
    assert 'frame-src' in csp
    for host in VIDEO_FRAME_HOSTS:
        assert host in csp


@pytest.mark.parametrize('newline', ['\r\n', '\r', '\n'])
def test_the_directive_survives_the_line_endings_a_browser_sends(newline):
    """A textarea is submitted with CRLF, so every body saved through the
    editor has them. This was live for one deploy: `$` in a multiline
    pattern stops before \\n and not before \\r, so the carriage return sat
    between the URL and the end of the line and nothing an author typed in
    the editor ever matched.
    """
    body = newline.join([f':::video {YOUTUBE}', '', '## TL;DR', '', 'Text.'])
    html = render_markdown(body)
    assert 'https://www.youtube-nocookie.com/embed/QMH4rPEJ5BI' in html
    assert ':::video' not in html


def test_the_email_link_survives_them_too():
    body = f':::video {YOUTUBE}\r\n\r\nText.\r\n'
    html = render_markdown(body, embed_videos=False)
    assert f'href="{YOUTUBE}"' in html
    assert ':::video' not in html
