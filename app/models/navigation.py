"""Organization-managed navigation: ordered links in named menus, with
one-level dropdown groups (a top-level item may hold children)."""

from sqlalchemy.orm import backref

from app.extensions import db
from app.platform.errors import ValidationError

from .base import BaseModel, OrgScoped
from .types import BigIntFK

MENUS = ('primary', 'footer')

# One-click starter column offered in Manage → Navigation when an
# organization has no footer columns (orgs provisioned before columns
# existed, or after deleting them all). Only routes every install has.
SUGGESTED_FOOTER_COLUMN = ('Explore', (
    ('Blog', '/blog'),
    ('Community', '/discussions'),
    ('Newsletter', '/subscribe'),
))


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
        # A top-level item may be label-only: it is a group (a dropdown in
        # the primary menu, a link column in the footer).
        if self.url and not (self.url.startswith(('http://', 'https://', '/', '#'))):
            raise ValidationError('URL must be absolute (http/https) or site-relative')
        if self.content_id and self.url:
            self.url = None         # a content link never also carries a URL
        if self.parent_id:
            if not (self.url or self.content_id):
                raise ValidationError('A link inside a group needs a page or URL')
            # A query, not session.get: get() can answer from the identity
            # map without emitting SQL, and the tenant filter only runs on
            # a real query.
            parent = NavigationItem.query.filter_by(id=self.parent_id).first()
            if parent is None or parent.menu != self.menu:
                raise ValidationError('Invalid parent item')
            if parent.parent_id is not None:
                raise ValidationError('Navigation nests one level only')
            if parent.url or parent.content_id:
                raise ValidationError('Links can only go inside a group, '
                                      'not another link')

    @property
    def href(self) -> str:
        if self.content_id and self.content:
            return self.content.permalink
        return self.url or '#'

    @property
    def is_group(self) -> bool:
        """A group is a top-level item with no destination of its own: a
        dropdown in the primary menu, a link column in the footer. Defined
        by shape, not by children, so a just-created empty group already
        renders (and edits) as a group."""
        return (self.parent_id is None
                and not self.url and not self.content_id)

    @classmethod
    def items_for(cls, menu: str):
        """Top-level items in order; children hang off .children."""
        return (cls.query.filter_by(menu=menu, parent_id=None)
                .order_by(cls.position, cls.id).all())

    @classmethod
    def top_level_for(cls, menu: str):
        return cls.items_for(menu)

    @classmethod
    def create_suggested_footer_column(cls):
        """Create the starter footer column for the current org. No-op when
        any column already exists, so the Manage button can't duplicate."""
        if any(item.is_group for item in cls.items_for('footer')):
            return
        heading, links = SUGGESTED_FOOTER_COLUMN
        group = cls(menu='footer', label=heading,
                    position=cls.next_position('footer'))
        group.save()
        for position, (label, url) in enumerate(links, start=1):
            cls(menu='footer', label=label, url=url,
                parent_id=group.id, position=position).save()

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
