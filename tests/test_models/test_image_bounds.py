"""An image is refused when it is too expensive to decode.

Compressed size does not predict the cost: a large image of one flat colour
is tiny on disk and enormous in memory. The guard reads the header for the
dimensions and refuses before anything decodes the pixels.

Most tests here build only a PNG header, with no pixel data at all. If the
guard ever started decoding before checking, these would fail rather than
quietly allocating gigabytes.
"""

import io
import math
import struct
import zlib

import pytest
from flask import g
from PIL import Image

from app.models.upload import MAX_PIXELS, Upload, open_bounded
from app.platform.errors import ValidationError


def _png_header(width: int, height: int) -> bytes:
    """A PNG that declares its size and carries no pixels. Under 100 bytes
    however large the declared image."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data)))

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(b'\x00' * 16))
            + chunk(b'IEND', b''))


def _square_of(pixels: int) -> int:
    return int(math.sqrt(pixels)) + 1


def _real_png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (width, height), 'white').save(out, 'PNG')
    return out.getvalue()


def _real_jpeg(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (width, height), 'white').save(out, 'JPEG')
    return out.getvalue()


def test_the_size_is_read_without_decoding():
    """The premise. A header alone is enough to refuse."""
    edge = _square_of(MAX_PIXELS)
    data = _png_header(edge, edge)
    assert len(data) < 100
    with pytest.raises(ValidationError, match='too large to process'):
        open_bounded(data)


def test_an_ordinary_camera_image_is_accepted():
    """A 48 megapixel phone is 8064x6048 and a 61 megapixel full frame is
    9504x6336. Refusing either would be a regression for real photos."""
    for width, height in ((4032, 3024), (8064, 6048), (9504, 6336)):
        assert width * height < MAX_PIXELS
        assert open_bounded(_png_header(width, height)).size == (width, height)


def test_an_image_over_pillows_own_limit_is_refused_cleanly():
    """Above twice MAX_PIXELS Pillow refuses inside open(), before the
    explicit check can report a size. It must surface as the same
    validation error, never as a 500."""
    edge = _square_of(MAX_PIXELS * 2)
    with pytest.raises(ValidationError, match='too large to process'):
        open_bounded(_png_header(edge, edge))


def test_a_file_that_is_not_a_readable_image_is_refused_cleanly():
    """Magic bytes say PNG, content disagrees. Storing it unreadable was
    the old behaviour; a 500 is not an acceptable replacement."""
    with pytest.raises(ValidationError, match='not a readable image'):
        open_bounded(b'\x89PNG\r\n\x1a\n' + b'A' * 64)


def test_the_avatar_path_refuses_an_oversized_image(app, user):
    from app.controllers.members import _set_avatar

    edge = _square_of(MAX_PIXELS)

    class _Part:
        stream = io.BytesIO(_png_header(edge, edge))
        filename = 'bomb.png'

    with app.test_request_context(), \
            pytest.raises(ValidationError, match='too large to process'):
        _set_avatar(user, _Part())


def test_the_upload_path_refuses_an_oversized_image(app, acme):
    edge = _square_of(MAX_PIXELS)

    class _Part:
        stream = io.BytesIO(_png_header(edge, edge))
        filename = 'bomb.png'

    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='too large to process'):
            Upload.from_file(_Part())


def test_a_normal_upload_still_works(app, acme):
    class _Part:
        stream = io.BytesIO(_real_png(64, 48))
        filename = 'ok.png'

    with app.test_request_context():
        g.org = acme
        upload = Upload.from_file(_Part())
        assert upload.content_type == 'image/png'
        assert upload.size > 0


def test_drafting_never_shrinks_the_stored_original(app, acme):
    """The original is downloadable, so re-encoding must keep its size.
    Only the paths that scale down may ask the decoder for less.

    This has to be a JPEG. Drafting is a no-op on PNG, so a PNG here would
    pass whether or not the original path asked for a smaller decode.
    """
    class _Part:
        stream = io.BytesIO(_real_jpeg(4032, 3024))
        filename = 'photo.jpg'

    with app.test_request_context():
        g.org = acme
        upload = Upload.from_file(_Part())
        from app.platform.storage import storage
        assert Image.open(storage().open(upload.key)).size == (4032, 3024)


def test_variants_are_the_same_size_a_full_decode_would_give(app, acme):
    """Drafting must not change what comes out the other end."""
    from app.models.upload import VARIANTS

    class _Part:
        stream = io.BytesIO(_real_jpeg(4000, 3000))
        filename = 'photo.jpg'

    with app.test_request_context():
        g.org = acme
        upload = Upload.from_file(_Part())
        assert upload.has_variants
        from app.platform.storage import storage
        for name, edge in VARIANTS.items():
            got = Image.open(storage().open(upload.variant_key(name)))
            assert max(got.size) == edge

def test_the_limit_itself_stays_within_reach_of_the_memory_it_implies():
    """Every other test here derives its sizes from MAX_PIXELS, so raising
    the constant would keep them green while reopening the hole.

    Measured cost reaches thirteen bytes a pixel for a transparent or
    palette image, not the four a single raster suggests, which puts this
    limit near 800 MB for one upload. Anything much higher is the same
    denial of service the guard exists to stop.
    """
    assert MAX_PIXELS <= 70_000_000


def test_a_truncated_image_is_refused_rather_than_crashing(app, acme, user):
    """The header parses and the pixels are missing, which is what an
    upload cut short by a dropped connection looks like. It has to reach
    the user as a message, not a server error."""
    from app.controllers.members import _set_avatar

    whole = _real_jpeg(800, 600)
    half = whole[:len(whole) // 2]

    class _Part:
        stream = io.BytesIO(half)
        filename = 'cut.jpg'

    with app.test_request_context(), \
            pytest.raises(ValidationError, match='could not be read in full'):
        _set_avatar(user, _Part())
