"""Uploaded files. See blueprint/patterns/storage.md.

The stored key is never the uploaded name; the type is sniffed from bytes;
every file has a row and rows are org-scoped like any business data.
"""

import io
import secrets

from flask import g

from app.extensions import db
from app.platform.errors import ValidationError
from .base import AuditMixin, BaseModel, OrgScoped

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


def _sanitize_raster(data: bytes, content_type: str) -> bytes:
    """Decode and re-encode a raster image, dropping EXIF and any smuggled
    payload. Returns original bytes if Pillow can't process it."""
    import io as _io
    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = 30_000_000
        img = Image.open(_io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        fmt = {'image/png': 'PNG', 'image/jpeg': 'JPEG',
               'image/webp': 'WEBP'}[content_type]
        if fmt == 'JPEG' and img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        out = _io.BytesIO()
        img.save(out, fmt)
        return out.getvalue()
    except Exception:       # noqa: BLE001 -- non-decodable: keep original bytes
        return data


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

        # Re-encode raster images so the STORED ORIGINAL is sanitized too:
        # strips EXIF (GPS) and drops anything hiding in the container. The
        # /files/<id>/original route is public, so this must not be
        # variant-only.
        if content_type in ('image/png', 'image/jpeg', 'image/webp'):
            head = _sanitize_raster(head, content_type)

        key = f'org/{g.org.id}/{secrets.token_hex(16)}{ext}'
        storage().save(key, io.BytesIO(head))

        upload = cls(key=key, filename=(file.filename or 'upload')[:255],
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
        from PIL import Image, ImageOps
        from app.platform.storage import storage

        Image.MAX_IMAGE_PIXELS = 30_000_000     # decompression-bomb guard
        img = Image.open(io.BytesIO(data))
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
