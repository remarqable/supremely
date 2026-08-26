"""Organization-managed navigation: ordered links in named menus, with
one-level dropdown groups (a top-level item may hold children)."""

from sqlalchemy.orm import backref

from app.extensions import db
from app.platform.errors import ValidationError

from .base import BaseModel, OrgScoped
from .types import BigIntFK

MENUS = ('primary', 'footer')


class NavigationItem(OrgScoped, BaseModel):
    __tablename__ = 'navigation_item'

    menu = db.Column(db.String(20), nullable=False, default='primary')
    label = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(500), nullable=True)
    content_id = db.Column(BigIntFK,
                           db.ForeignKey('content.id', ondelete='CASCADE'),
                           nullable=True)
    parent_id = db.Column(BigIntFK,
                          db.ForeignKey('navigation_item.id', ondelete='CASCADE'),
                          nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0)

    content = db.relationship('Content', lazy='select')
    children = db.relationship(
        'NavigationItem', order_by='NavigationItem.position',
        cascade='all, delete-orphan',
        backref=backref('parent', remote_side='NavigationItem.id'))

    __table_args__ = (
        db.Index('ix_navigation_item_org_menu', 'org_id', 'menu', 'position'),
    )

    def validate(self):
        self.label = (self.label or '').strip()
        self.menu = self.menu or 'primary'
        if not self.label:
            raise ValidationError('Label is required')
        if self.menu not in MENUS:
            raise ValidationError('Invalid menu')
        # A link may be label-only: it renders as a dropdown heading.
        if self.url and not (self.url.startswith(('http://', 'https://', '/', '#'))):
            raise ValidationError('URL must be absolute (http/https) or site-relative')
        if self.content_id and self.url:
            self.url = None         # a content link never also carries a URL
        if self.parent_id:
            parent = db.session.get(NavigationItem, self.parent_id)
            if parent is None or parent.menu != self.menu:
                raise ValidationError('Invalid parent item')
            if parent.parent_id is not None:
                raise ValidationError('Navigation nests one level only')

    @property
    def href(self) -> str:
        if self.content_id and self.content:
            return self.content.permalink
        return self.url or '#'

    @property
    def is_group(self) -> bool:
        return bool(self.children)

    @classmethod
    def items_for(cls, menu: str):
        """Top-level items in order; children hang off .children."""
        return (cls.query.filter_by(menu=menu, parent_id=None)
                .order_by(cls.position, cls.id).all())

    @classmethod
    def top_level_for(cls, menu: str):
        return cls.items_for(menu)

    @classmethod
    def next_position(cls, menu: str, parent_id=None) -> int:
        import sqlalchemy as sa
        current = db.session.scalar(
            sa.select(sa.func.max(cls.position))
            .where(cls.menu == menu, cls.parent_id.is_(None) if parent_id is None
                   else cls.parent_id == parent_id))
        return (current or 0) + 1

    def move(self, direction: int):
        """Swap position with the neighbor above (-1) or below (+1),
        within the same menu and parent."""
        neighbor_query = NavigationItem.query.filter_by(menu=self.menu,
                                                        parent_id=self.parent_id)
        if direction < 0:
            neighbor = (neighbor_query.filter(NavigationItem.position < self.position)
                        .order_by(NavigationItem.position.desc()).first())
        else:
            neighbor = (neighbor_query.filter(NavigationItem.position > self.position)
                        .order_by(NavigationItem.position).first())
        if neighbor is None:
            return self
        self.position, neighbor.position = neighbor.position, self.position
        db.session.commit()
        return self
