"""Organization-managed navigation: ordered links in named menus."""

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
    page_id = db.Column(BigIntFK, db.ForeignKey('page.id', ondelete='CASCADE'),
                        nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0)

    page = db.relationship('Page', lazy='select')

    __table_args__ = (
        db.Index('ix_navigation_item_org_menu', 'org_id', 'menu', 'position'),
    )

    def validate(self):
        self.label = (self.label or '').strip()
        if not self.label:
            raise ValidationError('Label is required')
        if self.menu not in MENUS:
            raise ValidationError('Invalid menu')
        if not self.url and not self.page_id:
            raise ValidationError('A link needs a page or a URL')
        if self.url and not (self.url.startswith(('http://', 'https://', '/', '#'))):
            raise ValidationError('URL must be absolute (http/https) or site-relative')

    @property
    def href(self) -> str:
        if self.page_id and self.page:
            return f'/{self.page.slug}'
        return self.url or '#'

    @classmethod
    def items_for(cls, menu: str):
        return (cls.query.filter_by(menu=menu)
                .order_by(cls.position, cls.id).all())

    @classmethod
    def next_position(cls, menu: str) -> int:
        import sqlalchemy as sa
        current = db.session.scalar(
            sa.select(sa.func.max(cls.position)).where(cls.menu == menu))
        return (current or 0) + 1

    def move(self, direction: int):
        """Swap position with the neighbor above (-1) or below (+1)."""
        neighbor_query = NavigationItem.query.filter_by(menu=self.menu)
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
