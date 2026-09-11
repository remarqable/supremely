"""The update check: is this installation behind the published image?

This is the only outbound request the application makes, so most of what is
here is about the ways it must not misbehave -- it must send nothing, it
must never speak in a request, and every failure must be silence rather
than a broken page.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.extensions import db
from app.models import InstallationSetting
from app.platform import updates


@pytest.fixture
def checking(tmp_path):
    """An installation that checks, built thirty days ago.

    Its own app with its own config, because config is passed into
    create_app and never assigned afterwards -- a fixture that mutates a
    shared one has to remember to put it back, and half-restored config is
    a test that fails somewhere else.
    """
    from app import create_app
    from app.config import TestConfig
    from app.extensions import db as database

    class Cfg(TestConfig):
        DATA_DIR = str(tmp_path)
        UPDATE_CHECK_ENABLED = True
        APP_BUILT_AT = (datetime.now(UTC) - timedelta(days=30)).isoformat()

    checking_app = create_app(Cfg)
    with checking_app.app_context():
        database.create_all()
        yield checking_app
        database.session.remove()
        database.drop_all()


def _published(**offset) -> None:
    InstallationSetting.set(
        updates.LATEST_KEY, (datetime.now(UTC) + timedelta(**offset)).isoformat())
    db.session.commit()


def test_a_newer_image_is_something_to_say(checking):
    with checking.test_request_context():
        _published(days=0)
        pending = updates.pending_update()

    assert pending is not None
    assert pending['days_behind'] == 30


def test_the_same_image_is_not(checking):
    """The registry records a push and the image records a build, minutes
    apart on a good day, and no clock involved is exact."""
    with checking.test_request_context():
        InstallationSetting.set(updates.LATEST_KEY,
                                checking.config['APP_BUILT_AT'])
        db.session.commit()
        assert updates.pending_update() is None


def test_a_push_minutes_after_the_build_is_the_same_build(checking):
    with checking.test_request_context():
        built = updates._parse(checking.config['APP_BUILT_AT'])
        InstallationSetting.set(
            updates.LATEST_KEY, (built + timedelta(minutes=20)).isoformat())
        db.session.commit()
        assert updates.pending_update() is None


def test_a_source_build_is_never_behind(checking):
    """Nagging somebody about the code they are editing is noise."""
    checking.config['APP_BUILT_AT'] = ''
    with checking.test_request_context():
        _published(days=0)
        assert updates.pending_update() is None


def test_switched_off_says_nothing(checking):
    checking.config['UPDATE_CHECK_ENABLED'] = False
    with checking.test_request_context():
        _published(days=0)
        assert updates.pending_update() is None


def test_never_having_asked_says_nothing(checking):
    """An installation with no outbound network is a supported way to run
    this, so it gets silence rather than a guess."""
    with checking.test_request_context():
        assert updates.pending_update() is None


def test_nonsense_from_the_network_is_not_a_date(checking):
    """Whatever is on the other end is not ours."""
    with checking.test_request_context():
        for rubbish in ('', 'soon', '2026-13-45', '{}', 'null'):
            InstallationSetting.set(updates.LATEST_KEY, rubbish)
            db.session.commit()
            assert updates.pending_update() is None, rubbish


def test_the_dismissal_token_moves_when_a_newer_image_lands(checking):
    """Dismissal is kept in the browser against this value, so dismissing
    one gap must not blind the operator to the next."""
    with checking.test_request_context():
        _published(days=0)
        first = updates.pending_update()['token']
        _published(days=1)
        second = updates.pending_update()['token']

    assert first != second


def test_a_failed_check_does_not_overwrite_what_we_knew(checking):
    """The last answer is still the best one available. Replacing it with
    "we could not ask today" would make one bad night look like an
    up-to-date installation."""
    with checking.test_request_context():
        _published(days=0)
        known = InstallationSetting.get_value(updates.LATEST_KEY)

        updates.record_check(None)

        assert InstallationSetting.get_value(updates.LATEST_KEY) == known
        assert updates.pending_update() is not None


class _Opener:
    """Stands in for the opener so nothing in these tests touches a socket."""

    def __init__(self, on_open):
        self._on_open = on_open

    def open(self, request, timeout=None):
        return self._on_open(request)


def _served(body: bytes):
    class _Response:
        def read1(self, size=None):
            data, self.done = (b'' if getattr(self, 'done', False) else body), True
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Response()


GOOD = b'{"last_updated": "2026-09-11T15:13:36.449224Z"}'


def test_the_request_says_nothing_about_this_installation(checking, monkeypatch):
    """The privacy claim, checked on the request that would go out.

    The User-Agent is the part worth asserting: urllib supplies its own
    when none is set, and its own names the interpreter's minor version.
    Setting one is what stops that, so this checks both that we set it and
    that what we set carries no version.
    """
    seen = {}

    def _open(request):
        seen['url'] = request.full_url
        seen['data'] = request.data
        seen['headers'] = {k.lower(): v for k, v in request.header_items()}
        return _served(GOOD)

    monkeypatch.setattr('urllib.request.build_opener',
                        lambda *handlers: _Opener(_open))
    with checking.test_request_context():
        assert updates.fetch_latest_built_at() is not None

    assert seen['data'] is None                      # no body
    assert '?' not in seen['url']                    # no query
    # Set explicitly, so urllib never substitutes Python-urllib/<version>.
    assert seen['headers']['user-agent'] == updates.USER_AGENT
    assert not any(char.isdigit() for char in updates.USER_AGENT)
    assert checking.config['APP_BUILT_AT'] not in str(seen)


def test_every_network_failure_is_the_same_silence(checking, monkeypatch):
    import urllib.error

    for failure in (urllib.error.URLError('no route'),
                    TimeoutError(),
                    OSError('connection reset'),
                    # urlopen raises this for a URL it cannot parse, which
                    # is what an operator gets for a typo in the setting.
                    ValueError('unknown url type'),
                    UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'bad')):
        def _raise(_request, _failure=failure):
            raise _failure

        monkeypatch.setattr('urllib.request.build_opener',
                            lambda *handlers, _r=_raise: _Opener(_r))
        with checking.test_request_context():
            assert updates.fetch_latest_built_at() is None


class _Trickler:
    """A loopback server that answers one byte at a time.

    A real socket, because the risk here cannot be reproduced with a stub.
    HTTPResponse.read(n) blocks until n bytes arrive; a fake whose read()
    returns one byte per call is a shape no real response has, and a test
    built on one certifies a bound that does not exist.
    """

    def __init__(self, body: bytes, gap: float, headers: int = 0):
        import socket
        import threading
        self.body, self.gap, self.headers = body, gap, headers
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        import time as clock
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.recv(65536)
            try:
                conn.sendall(b'HTTP/1.1 200 OK\r\n')
                for index in range(self.headers):
                    if self.stop.is_set():
                        return
                    conn.sendall(b'X-Pad-%d: x\r\n' % index)
                    clock.sleep(self.gap)
                conn.sendall(b'Content-Type: application/json\r\n')
                conn.sendall(b'Content-Length: %d\r\n\r\n' % len(self.body))
                for byte in self.body:
                    if self.stop.is_set():
                        return
                    conn.sendall(bytes([byte]))
                    clock.sleep(self.gap)
            except OSError:
                return

    def close(self):
        self.stop.set()
        self.sock.close()


def _against(app, server, monkeypatch):
    """Point the check at a loopback server, http and all."""
    monkeypatch.setattr(updates, 'ALLOWED_SCHEME', 'http')
    app.config['UPDATE_CHECK_URL'] = f'http://127.0.0.1:{server.port}/tags/latest'


def test_a_trickling_body_is_abandoned_not_waited_out(checking, monkeypatch):
    """The one that matters for the worker, over a real socket.

    jobs.md runs exactly one worker on SQLite, so a handler that waits is
    the newsletter and every notification stopped behind a stranger's
    server. A stub cannot show this: HTTPResponse.read(n) does not return
    short, so a loop around it never looks at the clock.
    """
    import time

    monkeypatch.setattr(updates, 'BUDGET', 1)
    server = _Trickler(GOOD, gap=0.5)
    try:
        _against(checking, server, monkeypatch)
        started = time.monotonic()
        with checking.test_request_context():
            assert updates.fetch_latest_built_at() is None
        elapsed = time.monotonic() - started
    finally:
        server.close()

    assert elapsed < 8, f'held the worker {elapsed:.1f}s against a 1s budget'


def test_trickling_headers_are_abandoned_too(checking, monkeypatch):
    """The status line and the headers are read inside open(), where no
    deadline of ours applies -- a hundred header lines is hours before a
    single byte of body arrives."""
    import time

    monkeypatch.setattr(updates, 'BUDGET', 1)
    server = _Trickler(GOOD, gap=0.5, headers=40)
    try:
        _against(checking, server, monkeypatch)
        started = time.monotonic()
        with checking.test_request_context():
            assert updates.fetch_latest_built_at() is None
        elapsed = time.monotonic() - started
    finally:
        server.close()

    assert elapsed < 8, f'held the worker {elapsed:.1f}s in the header phase'


def test_a_well_behaved_server_is_answered_normally(checking, monkeypatch):
    """The same loopback path, to show the bound does not break the case it
    is protecting."""
    server = _Trickler(GOOD, gap=0)
    try:
        _against(checking, server, monkeypatch)
        with checking.test_request_context():
            found = updates.fetch_latest_built_at()
    finally:
        server.close()

    assert found is not None
    assert found.year == 2026


def test_a_body_is_bounded_by_size_as_well(checking):
    class _Endless:
        def read1(self, size=None):
            return b'x' * (size or 1)

    with checking.test_request_context():
        body = updates._read_within(_Endless(), float('inf'))

    assert len(body) == updates.MAX_BYTES


def test_a_redirect_elsewhere_is_not_followed(checking):
    """The address is configuration. A redirect is another host asking to
    be asked instead, and nothing here needs to say yes."""
    handler = updates._NoRedirects()

    assert handler.redirect_request(
        None, None, 302, 'Found', {}, 'https://elsewhere.test/x') is None


def test_only_https_is_fetched(checking, monkeypatch):
    """A file:// URL in that setting should not read the disk."""
    opened = []
    monkeypatch.setattr('urllib.request.build_opener',
                        lambda *h: _Opener(lambda r: opened.append(r)))
    for url in ('file:///etc/hosts', 'http://hub.docker.com/v2/x', 'ftp://x/y'):
        checking.config['UPDATE_CHECK_URL'] = url
        with checking.test_request_context():
            assert updates.fetch_latest_built_at() is None

    assert opened == []


def test_switched_off_opens_no_socket(checking, monkeypatch):
    """The documented promise, enforced at the socket rather than only at
    the callers that remember to ask first."""
    opened = []
    monkeypatch.setattr('urllib.request.build_opener',
                        lambda *h: _Opener(lambda r: opened.append(r)))
    checking.config['UPDATE_CHECK_ENABLED'] = False
    with checking.test_request_context():
        assert updates.fetch_latest_built_at() is None

    assert opened == []


def test_a_failing_check_does_not_multiply_its_own_schedule(app, monkeypatch):
    """Through run_pending_jobs, which is where it went wrong.

    enqueue() commits, so a re-enqueue at the top of a handler outlives the
    rollback when the handler fails -- and the failed job is put back to
    pending with a backoff, leaving two. Each then books its own successor
    on the next failure, so a recurring job doubles every time and so does
    how often it talks to somebody else's server.
    """
    from app.models import Job
    from app.platform import jobs

    app.config['UPDATE_CHECK_ENABLED'] = True
    app.config['APP_BUILT_AT'] = datetime.now(UTC).isoformat()
    monkeypatch.setattr(updates, 'record_check',
                        lambda _latest: (_ for _ in ()).throw(OSError('down')))
    monkeypatch.setattr(updates, 'fetch_latest_built_at', lambda: None)
    try:
        with app.test_request_context():
            jobs.enqueue('system.update_check')
            for _ in range(3):          # three failed runs
                Job.query.filter_by(name='system.update_check').update(
                    {'run_at': datetime.now(UTC), 'locked_at': None})
                db.session.commit()
                jobs.run_pending_jobs()
            pending = Job.query.filter_by(name='system.update_check',
                                          status='pending').count()
    finally:
        app.config['UPDATE_CHECK_ENABLED'] = False
        app.config['APP_BUILT_AT'] = ''

    # Two at most: tomorrow's, booked before the handler could fail, and
    # the failed run itself waiting on its backoff. The point is that it
    # does not grow -- without the guard each failure doubled it -- and the
    # pair collapses to one as soon as a run succeeds.
    assert pending <= 2, f'{pending} copies of a once-a-day job'


def test_nothing_is_scheduled_when_the_check_is_off(app):
    """A job that wakes daily only to return is a job the operator asked
    not to run."""
    from app.models import Job
    from app.platform import jobs

    app.config['UPDATE_CHECK_ENABLED'] = False
    with app.test_request_context():
        jobs._seed_recurring_jobs()
        assert Job.query.filter_by(name='system.update_check').count() == 0
        assert Job.query.filter_by(name='system.cleanup').count() == 1
