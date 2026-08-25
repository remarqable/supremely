"""File storage: local disk behind a small interface (S3-capable later).

Files live on the data volume, never inside app/static. See
blueprint/patterns/storage.md.
"""

from pathlib import Path
from typing import BinaryIO

from flask import current_app


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError(f'Unsafe storage key: {key}')
        return p

    def save(self, key: str, stream: BinaryIO) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'wb') as f:
            for chunk in iter(lambda: stream.read(65536), b''):
                f.write(chunk)

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), 'rb')

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


def storage() -> LocalStorage:
    return LocalStorage(Path(current_app.config['DATA_DIR']) / 'uploads')
