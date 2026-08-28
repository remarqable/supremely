"""Uploaded files. See blueprint/patterns/storage.md.

The stored key is never the uploaded name; the type is sniffed from bytes;
every file has a row and rows are org-scoped like any business data.
"""

import io
import re
import secrets
from typing import TYPE_CHECKING

from flask import g

from app.extensions import db
from app.platform.errors import ValidationError

from .base import AuditMixin, BaseModel, OrgScoped

if TYPE_CHECKING:
    from PIL.ImageFile import ImageFile

# Sniffed from magic bytes -- extensions and client headers are untrusted.
MAGIC = (
    (b'\x89PNG\r\n\x1a\n', 'image/png', '.png'),
    (b'\xff\xd8\xff', 'image/jpeg', '.jpg'),
    (b'GIF87a', 'image/gif', '.gif'),
    (b'GIF89a', 'image/gif', '.gif'),
    (b'%PDF-', 'application/pdf', '.pdf'),
    (b'\x00\x00\x01\x00', 'image/x-icon', '.ico'),
)

RASTER_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
VARIANTS = {'thumb': 200, 'medium': 800, 'full': 1600}   # max long edge, px
MAX_SIZE = 10 * 1024 * 1024

# Opening an image costs up to thirteen bytes a pixel here, not the four a
# single raster suggests: rotating returns a copy, re-encoding holds
# another, and a palette or transparent image becomes four bytes a pixel
# first. Compressed size predicts none of it, since a 8000x8000 PNG of one
# flat colour is 200 KB on disk and around 800 MB to process. This ceiling
# clears every phone and consumer camera, including a 61 megapixel full
# frame; how often someone may spend it is the rate limits' job.
MAX_PIXELS = 64_000_000


def open_bounded(data: bytes, draft_to: int | None = None) -> 'ImageFile':
    """Open an image, refusing one too large to decode safely.

    Image.open reads the header and stops, so the size check runs before a
    pixel is allocated. Pillow's own MAX_IMAGE_PIXELS is not that check: it
    warns at the limit and only raises at twice it.

    Pass draft_to only when the result is about to be scaled down anyway.
    It asks the decoder for a smaller image, which a JPEG can do cheaply,
    and would otherwise reduce an original a visitor can download.
    """
    from PIL import Image, UnidentifiedImageError

    # Pillow refuses inside open() at twice this, before the check below
    # runs. Pinning it here keeps the two limits from disagreeing.
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        img = Image.open(io.BytesIO(data))
    except (Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        # Refused inside open(), before a size could be reported.
        raise ValidationError(
            f'Image too large to process (max {MAX_PIXELS // 1_000_000} '
            'megapixels)') from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        # Magic bytes said image, content disagreed. Better to say so than
        # to store a file that can never be displayed.
        raise ValidationError('That file is not a readable image') from exc

    width, height = img.size
    if width * height > MAX_PIXELS:
        raise ValidationError(
            f'Image too large to process (max {MAX_PIXELS // 1_000_000} '
            f'megapixels, this one is {width}x{height})')
    if draft_to:
        # A no-op where the format cannot do it. JPEG decodes at 1/2, 1/4
        # or 1/8 scale for a fraction of the work.
        img.draft(None, (draft_to, draft_to))
    return img


def _sanitize_raster(data: bytes, content_type: str) -> bytes:
    """Decode and re-encode a raster image, dropping EXIF and any smuggled
    payload.

    Refuses rather than falling back to the uploaded bytes. Returning them
    was quiet and looked harmless, but the caller had no way to tell, so a
    file that failed here was stored with its location data intact and
    served from the /files/<id>/original route as though it had been
    cleaned.
    """
    import io as _io

    from PIL import ImageOps

    # No draft: this re-encodes the original a visitor can download,
    # so it must keep full resolution.
    img = open_bounded(data)
    try:
        img = ImageOps.exif_transpose(img)
        fmt = {'image/png': 'PNG', 'image/jpeg': 'JPEG',
               'image/webp': 'WEBP'}[content_type]
        if fmt == 'JPEG' and img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        out = _io.BytesIO()
        img.save(out, fmt)
    except (OSError, ValueError) as exc:
        raise ValidationError('That image could not be read in full') from exc
    return out.getvalue()


CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f]')


def safe_filename(name: str | None) -> str:
    """The stored name is handed to send_file as the download name.

    A line break in a header makes Werkzeug refuse to build the response,
    so one poisoned name turns every later request for that file into a
    server error. Multipart parsing lets an encoded one through.
    """
    return CONTROL_CHARS.sub('', (name or 'upload').strip())[:255] or 'upload'


def sniff(head: bytes) -> tuple[str, str] | None:
    for magic, content_type, ext in MAGIC:
        if head.startswith(magic):
            return content_type, ext
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    return None


class Upload(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'upload'

    key = db.Column(db.String(255), unique=True, nullable=False)
    filename = db.Column(db.String(255), nullable=False)     # original, display only
    content_type = db.Column(db.String(100), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    # public: anyone with the URL; private: members of the owning org
    visibility = db.Column(db.String(10), nullable=False, default='public')
    has_variants = db.Column(db.Boolean, nullable=False, default=False)

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith('image/')

    def variant_key(self, variant: str) -> str:
        if variant == 'original':
            return self.key
        stem = self.key.rsplit('.', 1)[0]
        return f'{stem}_{variant}.webp'

    def url(self, variant: str = 'original') -> str:
        return f'/files/{self.id}/{variant}'

    @classmethod
    def from_file(cls, file, visibility: str = 'public') -> 'Upload':
        """file: a werkzeug FileStorage from request.files."""
        from app.platform.storage import storage

        head = file.stream.read(MAX_SIZE + 1)
        if len(head) > MAX_SIZE:
            raise ValidationError('File too large (max 10 MB)')
        if not head:
            raise ValidationError('Empty file')

        sniffed = sniff(head)
        if sniffed is None:
            raise ValidationError('File type not allowed')
        content_type, ext = sniffed

        # Re-encoding below covers PNG, JPEG and WebP. This also bounds a
        # GIF, which is stored as sent and so is never opened again.
        if content_type in RASTER_TYPES:
            open_bounded(head)

        # Re-encode raster images so the STORED ORIGINAL is sanitized too:
        # strips EXIF (GPS) and drops anything hiding in the container. The
        # /files/<id>/original route is public, so this must not be
        # variant-only.
        if content_type in ('image/png', 'image/jpeg', 'image/webp'):
            head = _sanitize_raster(head, content_type)

        key = f'org/{g.org.id}/{secrets.token_hex(16)}{ext}'
        storage().save(key, io.BytesIO(head))

        upload = cls(key=key, filename=safe_filename(file.filename),
                     content_type=content_type, size=len(head),
                     visibility=visibility)
        upload.stamp_audit()
        upload.save()

        if content_type in ('image/png', 'image/jpeg', 'image/webp'):
            try:
                upload.make_variants(head)
                upload.has_variants = True
                upload.save()
            except Exception:       # noqa: BLE001 -- variants are best-effort
                pass
        return upload

    def make_variants(self, data: bytes) -> None:
        from PIL import ImageOps

        from app.platform.storage import storage

        # Every variant is scaled down, so a reduced decode costs less
        # and changes nothing in the output.
        img = open_bounded(data, draft_to=max(VARIANTS.values()))
        img = ImageOps.exif_transpose(img)      # honour rotation, then strip EXIF
        if img.mode in ('P', 'CMYK'):
            img = img.convert('RGBA' if img.mode == 'P' else 'RGB')

        for name, edge in VARIANTS.items():
            copy = img.copy()
            copy.thumbnail((edge, edge))
            out = io.BytesIO()
            copy.save(out, 'WEBP', quality=82)  # re-encode: also the sanitizer
            out.seek(0)
            storage().save(self.variant_key(name), out)

    def purge_files(self) -> None:
        from app.platform.storage import storage
        storage().delete(self.key)
        if self.has_variants:
            for name in VARIANTS:
                storage().delete(self.variant_key(name))

    def delete(self):
        self.purge_files()
        super().delete()
