"""Whether this installation has fallen behind the published image.

Supremely does not update itself, and nothing used to say so. An
installation can run months-old code while everybody assumes it is current,
which is how a live community ended up well behind: nobody was told, so
nobody looked.

This is the first outbound request the application makes. Everything else
it talks to is either the database or an SMTP server the operator
configured, and SMTP is optional by design -- so the shape of this is
deliberate rather than incidental:

It sends nothing about the installation. The check is a plain GET of a
public address: no query, no body, and one header naming the product but
not its version, because urllib's own default announces the interpreter's
minor version and that is a fact about the server nobody asked us to
publish. The comparison happens here, against a timestamp already baked
into the image. The other end learns that somebody asked, which is what it
learns from a `docker pull` anyway.

It can be switched off with one environment variable, and it says so in the
documentation rather than in a preference nobody finds.

It never runs in a request. A blocking call to somebody else's server while
a page renders is a hang waiting for the first firewalled installation, so
it runs on the worker and leaves its answer behind for the page to read.

It fails silently. No network, blocked DNS, an air-gapped install, Docker
Hub down or changed: no banner, no error, no log line every hour. The
banner appears only when there is something definite to say.

Why the registry rather than a file in the repository: the registry is the
artifact. A version file can name a release whose image never pushed, and
then every installation in the world is told to upgrade to something that
does not exist. The tag list cannot lie about what is pullable, because it
is what is pullable.

Why a timestamp rather than a tag: the published tags are the releases in
the changelog, and images are pushed far more often than releases are cut,
so between them the only thing that moves is `latest`. Comparing build
times answers "has anything been published since the image I am running",
which is the question, and needs no tag per build cluttering the registry.
"""

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from urllib.parse import urlsplit

from flask import current_app

from app.platform.logger import get_logger

log = get_logger()

# Where the answer is kept between checks. InstallationSetting rather than a
# column: this is one fact about the installation, not about any
# organization, and it wants no schema of its own.
LATEST_KEY = 'updates.latest_built_at'

# Per socket operation. Not a budget for the whole fetch: a server that
# sends one byte every nine seconds never trips it, so this alone would let
# a trickling response hold the worker for days. BUDGET below is the real
# bound and this just keeps a dead connection from waiting long.
TIMEOUT = 10

# Wall clock for the entire fetch, checked while reading. jobs.md has
# exactly one worker on SQLite, which is the default engine, so a handler
# that blocks blocks the newsletter and every notification behind it.
BUDGET = 20

# The document wanted is well under a kilobyte. Read in pieces so the
# budget can be checked between them rather than after the last one.
MAX_BYTES = 64_000
CHUNK = 8_192

# What the request says it is. urllib's default announces the interpreter's
# minor version -- Python-urllib/3.12 -- which is a fact about the server
# nobody asked us to publish. The product name is not new information: the
# other end already knows, because of which repository is being asked
# about. The version is deliberately absent.
USER_AGENT = 'Supremely'

# https only, and no redirects. This is a fixed address from configuration,
# so a redirect somewhere else is not something to follow quietly, and a
# file:// URL in that setting should not read the disk.
ALLOWED_SCHEME = 'https'

# Where "How to update" goes. Here rather than in the translation catalogue:
# plugin catalogues are merged process-wide at boot and win key collisions,
# so a URL kept there is a link any installed plugin could retarget -- and
# this one is only ever clicked by somebody who administers the
# installation. A link target is configuration, not copy.
UPDATE_GUIDE_URL = ('https://github.com/remarqable/supremely'
                    '/blob/main/docs/deployment/manual.md#upgrading')

# Below this, the two builds are the same build. The registry records the
# push and the image records the build, which are minutes apart on a good
# day, and no clock involved is exact.
GRACE = timedelta(hours=1)


class PendingUpdate(TypedDict):
    """What the console banner draws, and nothing else.

    Named rather than a bare dict so the keys are written down in one
    place; the template is Jinja and no checker reads it, so this documents
    the contract rather than enforcing it.
    """
    days_behind: int
    token: str
    guide_url: str


def enabled() -> bool:
    return bool(current_app.config.get('UPDATE_CHECK_ENABLED'))


def built_at() -> datetime | None:
    """When the running image was built, or None from source.

    Baked in at image build time beside APP_BUILD. A source checkout has no
    build to be behind, and nagging somebody who is running the code they
    are editing would be noise.
    """
    return _parse(current_app.config.get('APP_BUILT_AT', ''))


def _parse(value: str | None) -> datetime | None:
    """An ISO-8601 instant as an aware datetime, or None for anything else.

    Whatever is on the other end of the network is not ours, and neither is
    an environment variable somebody set by hand. Both arrive here and
    neither is trusted to be a date.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse to be sent somewhere else.

    The address is configuration, so a redirect is another host asking to
    be asked instead. Nothing about this needs to follow one, and not
    following is how the promise about where the request goes stays true.
    """

    def redirect_request(self, req: object, fp: object, code: int,
                         msg: str, headers: object,
                         newurl: str) -> None:
        return None


def _read_within(response: http.client.HTTPResponse,
                 budget_ends: float) -> bytes:
    """The body, up to MAX_BYTES and only while there is time left.

    read1, not read. HTTPResponse.read(n) blocks until n bytes arrive or
    the body ends, so a loop around it never gets to look at the clock: a
    server dribbling a byte at a time is swallowed whole inside the first
    call. read1 hands back whatever has arrived, which is what makes the
    deadline between iterations reachable at all.
    """
    chunks: list[bytes] = []
    size = 0
    while size < MAX_BYTES:
        if time.monotonic() > budget_ends:
            raise TimeoutError('update check exceeded its time budget')
        chunk = response.read1(min(CHUNK, MAX_BYTES - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b''.join(chunks)


def _fetch(url: str) -> datetime | None:
    """The request itself, off the worker's thread. See fetch_latest_built_at.

    Takes the address rather than reading configuration, because this does
    not run inside an application context.
    """
    budget_ends = time.monotonic() + BUDGET
    try:
        request = urllib.request.Request(
            url, headers={'Accept': 'application/json',
                          'User-Agent': USER_AGENT})
        opener = urllib.request.build_opener(_NoRedirects)
        with opener.open(request, timeout=TIMEOUT) as response:
            payload = json.loads(
                _read_within(response, budget_ends).decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, OSError,
            http.client.HTTPException, ValueError) as exc:
        # HTTPException covers what http.client raises on a malformed
        # response -- too many header lines, a bad status line, a body that
        # stops early -- none of which are OSError and all of which reach
        # here from a server having a bad day.
        #
        # ValueError rather than JSONDecodeError alone, which it covers:
        # urlopen raises a bare one for a URL it cannot parse, so an
        # operator who mistypes UPDATE_CHECK_URL gets the same silence as
        # an unreachable network instead of a job that dies every day.
        # UnicodeDecodeError is a ValueError too.
        log.debug('update_check_unavailable', error=str(exc)[:200])
        return None
    return _parse(payload.get('last_updated') if isinstance(payload, dict)
                  else None)


def fetch_latest_built_at() -> datetime | None:
    """When the newest published image was pushed, or None if we cannot say.

    One GET, no body, no query, and one header naming the product without
    its version.

    Run on a thread the worker is willing to walk away from, because
    nothing inside urllib can be trusted to come back. The socket timeout
    bounds one operation, not the exchange: a server sending a byte every
    nine seconds trips no timeout, and the status line and headers are read
    inside open() where no deadline of ours applies at all -- a hundred
    header lines of sixty-four kilobytes each is hours before a single byte
    of body. jobs.md runs exactly one worker on SQLite, which is the
    default engine, so a handler that waits is the newsletter and every
    notification waiting behind somebody else's server.

    Abandoning the thread leaves it holding a socket until the connection
    times out. That is one thread, once a day, doing no database work and
    holding nothing else -- it is a daemon, so it does not even hold the
    process open on the way out. A far better trade than a queue that
    stops.
    """
    if not enabled():
        # The off switch, at the socket rather than only at the callers.
        # What the documentation promises is that nothing opens a
        # connection, and that promise should not depend on every future
        # caller remembering to ask first.
        return None
    url = current_app.config.get('UPDATE_CHECK_URL', '')
    if not url or urlsplit(url).scheme != ALLOWED_SCHEME:
        return None
    # A daemon thread rather than a pool. An executor registers its threads
    # for joining at interpreter exit, so walking away from one leaves the
    # process unable to leave: `flask jobs work-off` returned on time and
    # then sat for minutes waiting on a socket nobody was reading. A daemon
    # thread is not joined at exit, so abandoning it really is abandoning
    # it, and the socket timeout ends it soon enough either way.
    answer: dict[str, datetime | None] = {}

    def run() -> None:
        answer['value'] = _fetch(url)

    thread = threading.Thread(target=run, name='update-check', daemon=True)
    thread.start()
    thread.join(timeout=BUDGET)
    if thread.is_alive():
        log.debug('update_check_gave_up', seconds=BUDGET)
        return None
    return answer.get('value')


def record_check(latest: datetime | None) -> None:
    """Keep what was found, if anything was.

    Nothing is written when the check came back empty: the last answer is
    still the best one available, and overwriting it with "we could not
    ask today" would make one bad night look like an up-to-date
    installation.
    """
    from app.models import InstallationSetting
    if latest is not None:
        InstallationSetting.set(LATEST_KEY, latest.isoformat())


def pending_update() -> PendingUpdate | None:
    """What the banner needs, or None when there is nothing to say.

    Reads what the worker left behind; never the network. None whenever
    anything is unknown -- switched off, a source build, never successfully
    checked, or a published image no newer than this one -- so the banner
    is drawn only on a definite answer.
    """
    if not enabled():
        return None
    mine = built_at()
    if mine is None:
        return None
    from app.models import InstallationSetting
    latest = _parse(InstallationSetting.get_value(LATEST_KEY))
    if latest is None or latest - mine <= GRACE:
        return None
    return {
        # The true count, not a floor of one: an hour past the grace is
        # not "about a day behind", and the banner picks its wording
        # from this.
        'days_behind': (latest - mine).days,
        # The banner is dismissed in the browser against this value, so a
        # dismissal covers the gap that was dismissed and nothing later:
        # when a newer image lands, the key changes and the banner returns.
        # No row, no column, no migration for a preference that only
        # matters to one person at one keyboard.
        'token': str(int(latest.timestamp())),
        'guide_url': UPDATE_GUIDE_URL,
    }
