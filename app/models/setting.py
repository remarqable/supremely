"""Installation-level settings (key-value). Not org-scoped."""

from app.extensions import db
from app.platform.errors import ValidationError

from .base import LIKE_ESCAPE, BaseModel, escape_like


class InstallationSetting(BaseModel):
    __tablename__ = 'installation_setting'

    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=False, default='')

    def validate(self):
        self.key = (self.key or '').strip()
        if not self.key:
            raise ValidationError('Setting key is required')

    @classmethod
    def get_value(cls, key: str, default: str = '') -> str:
        setting = cls.query.filter_by(key=key).first()
        return setting.value if setting else default

    @classmethod
    def get_bool(cls, key: str, default: bool = False) -> bool:
        value = cls.get_value(key, 'true' if default else 'false')
        return value.strip().lower() in ('true', '1', 'yes', 'on')

    @classmethod
    def set(cls, key: str, value: str) -> 'InstallationSetting':
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = cls(key=key, value=value)
        return setting.save()

    @classmethod
    def get_map(cls, prefix: str = '') -> dict:
        query = cls.query
        if prefix:
            query = query.filter(cls.key.ilike(f'{escape_like(prefix)}%',
                                               escape=LIKE_ESCAPE))
        return {s.key: s.value for s in query.all()}
