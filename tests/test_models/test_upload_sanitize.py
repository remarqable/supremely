"""What gets stored after an upload is checked, not assumed.

Two habits made the stored file untrustworthy. Re-encoding fell back to the
uploaded bytes whenever anything went wrong, so a file that failed to clean
was published as though it had been cleaned. And the name the visitor sent
was stored as typed, where a line break in it broke every later request for
that file.
"""

import io

import pytest
from flask import g
from PIL import Image

from app.models.upload import Upload, safe_filename
from app.platform.errors import ValidationError


def _jpeg_with_metadata() -> bytes:
    """GPS lives in the same block as these, so stripping is provable
    without hand building a GPS IFD."""
    exif = Image.Exif()
    exif[0x010F] = 'TestCam'          # Make
    exif[0x0110] = 'TestModel'        # Model
    exif[0x0132] = '2026:08:28 10:00:00'
    out = io.BytesIO()
    Image.new('RGB', (64, 48), 'white').save(out, 'JPEG', exif=exif)
    return out.getvalue()


class _Part:
    def __init__(self, data: bytes, filename: str = 'x.png'):
        self.stream = io.BytesIO(data)
        self.filename = filename


def test_a_file_that_cannot_be_cleaned_is_refused_not_stored(app, acme):
    """A truncated image parses its header and fails on the pixels. It used
    to be stored exactly as uploaded."""
    whole = io.BytesIO()
    Image.new('RGB', (400, 300), 'white').save(whole, 'PNG')
    half = whole.getvalue()[:len(whole.getvalue()) // 2]

    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError):
            Upload.from_file(_Part(half))
        assert Upload.query.count() == 0


def test_the_stored_original_really_is_stripped(app, acme):
    """The reason the fallback mattered: this is the promise it broke."""
    from app.platform.storage import storage

    with app.test_request_context():
        g.org = acme
        source = _jpeg_with_metadata()
        assert dict(Image.open(io.BytesIO(source)).getexif())

        upload = Upload.from_file(_Part(source, 'holiday.jpg'))
        stored = Image.open(storage().open(upload.key))
        assert not dict(stored.getexif())


def test_a_name_that_cannot_travel_in_a_header_is_cleaned():
    assert safe_filename('evil\r\nX-Injected: 1.png') == 'evilX-Injected: 1.png'
    assert safe_filename('ok.png') == 'ok.png'
    assert safe_filename(None) == 'upload'
    assert safe_filename('') == 'upload'
    assert safe_filename('\r\n') == 'upload'
    assert len(safe_filename('a' * 400)) == 255


def test_a_poisoned_name_no_longer_breaks_the_file_route(app, client, acme, user):
    """Werkzeug refuses to build a response whose header holds a newline, so
    one such name used to make every request for that file a 500."""
    from tests.conftest import login_as

    small = io.BytesIO()
    Image.new('RGB', (32, 32), 'white').save(small, 'PNG')

    with app.test_request_context():
        g.org = acme
        upload = Upload.from_file(_Part(small.getvalue(), 'a\r\nb.png'))
        assert '\r' not in upload.filename
        assert '\n' not in upload.filename
        upload_id = upload.id

    login_as(client, user)
    got = client.get(f'/files/{upload_id}/original',
                     base_url='http://acme.example.test')
    assert got.status_code == 200
