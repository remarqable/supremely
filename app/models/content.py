"""Content: the universal publishing model. Every published thing an
organization has -- pages, blog articles, events, and vertical types -- is a
Content row with a `type`. The type (see app/platform/content_types.py)
decides labels, URL base, template, and fields.

("Content" is deliberately distinct from a discussion Post; see
app/models/discussion.py.)
"""

import json
import re
import secrets
from typing import TYPE_CHECKING

import sqlalchemy as sa

from app.extensions import db
from app.platform.errors import ValidationError
from app.platform.theming import PAGE_TEMPLATE_RE

if TYPE_CHECKING:
    from flask_sqlalchemy.query import Query

    from app.platform.content_types import ContentType

from .base import (
    DERIVED_SLUG_MAX,
    LIKE_ESCAPE,
    AuditMixin,
    BaseModel,
    MarkdownBody,
    OrgScoped,
    escape_like,
    reject_control_characters,
    scoped_to_own_org,
    slugify,
    utcnow,
)
from .types import BigIntFK, JSONColumn, TZDateTime

# An article body is longer than a forum post, but still bounded: it is
# re-rendered through Markdown and the sanitiser on every view.
BODY_MAX = 500_000

content_category = db.Table(
    'content_category',
    db.Column('content_id', BigIntFK,
              db.ForeignKey('content.id', ondelete='CASCADE'), primary_key=True),
    db.Column('category_id', BigIntFK,
              db.ForeignKey('category.id', ondelete='CASCADE'), primary_key=True),
)


class Category(OrgScoped, BaseModel):
    __tablename__ = 'category'

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), nullable=False)
    # One of ICONS, or None for the fallback. A name, never markup: the
    # drawing lives in the category_icon macro, so a value from the database
    # can never put SVG on a page.
    icon = db.Column(db.String(30), nullable=True)
    # A starting skeleton for a new item in this category -- the headings a
    # review or an interview always has. Offered in the editor, never
    # imposed: it fills an empty body and is then the author's text like any
    # other, so changing it here does not touch anything already written.
    body_template = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'slug', name='uq_category_org_slug'),
    )

    # The icon catalogue an admin picks from. Names only; every one of these
    # must be drawn by the category_icon macro in partials/_ui.html, which a
    # test asserts.
    ICONS = ('tag', 'book', 'chat', 'calendar', 'video', 'mic', 'file',
             'users', 'star', 'lightbulb', 'rocket', 'chart', 'shield',
             'globe', 'heart', 'wrench')

    def validate(self):
        self.name = (self.name or '').strip()
        self.slug = (self.slug or '').strip().lower()
        if not self.name:
            raise ValidationError('Category name is required')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,98})?', self.slug):
            raise ValidationError('Category slug must be lowercase letters, numbers, hyphens')
        self.icon = (self.icon or '').strip() or None
        if self.icon is not None and self.icon not in self.ICONS:
            raise ValidationError('Unknown icon')
        self.body_template = (self.body_template or '').strip() or None
        # Bounded for the same reason a body is: it becomes one.
        if self.body_template and len(self.body_template) > BODY_MAX:
            raise ValidationError('Template too long')
        existing = scoped_to_own_org(
            Category.query.filter_by(slug=self.slug), self).first()
        if existing and existing.id != self.id:
            raise ValidationError('A category with that slug already exists')

    @classmethod
    def get_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower()).first()

    # How many pill colours the stylesheet defines (.pill-0 … .pill-N).
    PILL_TONES = 6

    @property
    def tone(self) -> int:
        """Colour index for this category's pill.

        Taken from the row id, which is sequential, so categories created
        one after another get different colours and a row of six is six
        colours. Hashing the slug was tried first and clustered badly --
        three of the first six categories landed on the same tone, which
        reads as a bug rather than a palette.

        The id never changes, so a category keeps its colour for life and
        looks the same on every archive that draws it. Deleting and adding
        categories can eventually collide two of them; that is cosmetic,
        and worth it for needing no column and no query.
        """
        return (self.id or 0) % self.PILL_TONES

    @classmethod
    def for_type(cls, type_slug: str) -> list['Category']:
        """Categories holding at least one item of one type this visitor
        may see.

        A category is shared across content types, so the blog must not
        offer one that only events use. Visibility rides on
        Content.visible_query, which means a category whose only items are
        gated disappears for a visitor when teasing is off, and stays as a
        locked-item filter when it is on.
        """
        visible_ids = (Content.visible_query(type_slug)
                       .order_by(None)
                       .with_entities(Content.id))
        used = (db.session.query(content_category.c.category_id)
                .filter(content_category.c.content_id.in_(visible_ids))
                .distinct())
        return cls.query.filter(cls.id.in_(used)).order_by(cls.name).all()


# Slugs a page-type content cannot use, because a page is served at /<slug>
# and must not shadow app routes. Feed-type bases are added dynamically.
# Taken under every archive, because every type's items live at
# /<base>/<slug> and this is that type's own address. Its sibling
# /<base>/feed.atom needs no entry: a slug cannot hold a dot, so nothing
# can ever be asked to take it.
#
# Whatever is in here has to be in _free_slug's reserved set as well, or an
# author whose title derives to one of these is refused a save over a field
# they never filled in.
RESERVED_ITEM_SLUGS = {'feed'}

RESERVED_PAGE_SLUGS = {
    'manage', 'dashboard', 'admin', 'auth', 'setup', 'static', 'files',
    'themes', 'launcher', 'health', 'discussions', 'members', 'newsletter',
    'feed', 'sitemap', 'profile', 'invite', 'avatars', 'subscribe',
    'unsubscribe',
    # Real routes that were missing from this list. A slug is derived from a
    # title now, so an ordinary word like "Notifications" reaches them by
    # accident where before somebody had to type it deliberately.
    'notifications', 'newsletters', 'glossary', 'tls-check', '_v',
    'rsvp', 'reset',
    # Syndication and discovery, mounted at the root (controllers/feeds.py).
    # Here for the mounted-prefix invariant rather than to stop a page
    # taking one: a slug cannot hold a dot, so the regex above refuses
    # these several lines before this set is consulted.
    'feed.atom', 'sitemap.xml', 'robots.txt',
}


class Content(OrgScoped, AuditMixin, MarkdownBody, BaseModel):
    __tablename__ = 'content'

    type = db.Column(db.String(50), nullable=False, default='article')
    title = db.Column(db.String(200), nullable=False)
    # Null for a child: a block inside an article has no address of its own.
    slug = db.Column(db.String(200), nullable=True)
    # The item this one lives inside, if any. A block is an ordinary content
    # row with a parent -- same registry, same fields, same renderer, same
    # validation -- rather than a second kind of thing with its own of each.
    # A child that earns its own page loses its parent and gains a slug; no
    # conversion between shapes, which is the whole reason it is one table.
    parent_id = db.Column(BigIntFK,
                          db.ForeignKey('content.id', ondelete='CASCADE',
                                        name='fk_content_parent_id'),
                          nullable=True)
    position = db.Column(db.Integer, nullable=True)
    body = db.Column(db.Text, nullable=False, default='')
    # The teaser a non-member sees in place of a gated item. It began as a
    # hand-written summary, was dropped when every item was made to
    # summarise from its own body (excerpt_or_summary), and is kept because
    # what organizations had written was worth keeping. It has one clear
    # job now: a body cut off mid-sentence is a poor argument for joining.
    excerpt = db.Column(db.String(500), nullable=True)
    featured_upload_id = db.Column(BigIntFK,
                                   db.ForeignKey('upload.id', ondelete='SET NULL'),
                                   nullable=True)
    fields = db.Column(JSONColumn, nullable=False, default=dict)
    tags = db.Column(JSONColumn, nullable=False, default=list)
    status = db.Column(db.String(10), nullable=False, default='draft')
    # Wide enough for `tier:<slug>` as well as the base levels
    # (app/platform/authz.py).
    visibility = db.Column(db.String(40), nullable=False, default='public')
    published_at = db.Column(TZDateTime, nullable=True)
    template = db.Column(db.String(50), nullable=True)   # page-type override
    # Where a standalone page presents: 'site' renders through the theme
    # (marketing header/footer), 'community' inside the app-owned shell.
    # Presentation, never authorization — visibility gates either way.
    presentation = db.Column(db.String(10), nullable=False, default='site')
    seo_title = db.Column(db.String(200), nullable=True)
    seo_description = db.Column(db.String(300), nullable=True)

    categories = db.relationship('Category', secondary=content_category,
                                 lazy='select')
    featured_upload = db.relationship('Upload', lazy='select')
    # delete-orphan as well as cascade: detaching a child from its parent in
    # Python has to mean the same thing as ON DELETE CASCADE means in the
    # database, or the two disagree about what a parentless block is.
    children = db.relationship('Content', lazy='select',
                               order_by='Content.position, Content.id',
                               cascade='all, delete-orphan',
                               backref=db.backref('parent', remote_side='Content.id'))

    __table_args__ = (
        # Two unique rules, because there are two address spaces. A
        # standalone item's address is unique across the organization; a
        # block's is unique inside the item it belongs to, so two courses
        # can each hold a lesson at .../lessons/one, which is the obvious
        # thing to want and what a single organization-wide rule refuses.
        #
        # Partial indexes rather than one constraint over both, because a
        # constraint including parent_id would treat every standalone row as
        # distinct from every other -- nulls do not compare equal -- and two
        # articles could then share an address. Both engines have supported
        # partial indexes for a decade, and the suite runs on both.
        db.Index('uq_content_org_type_slug', 'org_id', 'type', 'slug',
                 unique=True,
                 sqlite_where=db.text('parent_id IS NULL'),
                 postgresql_where=db.text('parent_id IS NULL')),
        db.Index('uq_content_parent_type_slug',
                 'org_id', 'parent_id', 'type', 'slug', unique=True,
                 sqlite_where=db.text('parent_id IS NOT NULL'),
                 postgresql_where=db.text('parent_id IS NOT NULL')),
        db.Index('ix_content_org_type_status',
                 'org_id', 'type', 'status'),
        db.Index('ix_content_parent_position', 'org_id', 'parent_id', 'position'),
    )

    # This is the publishing surface, so :::embed and :::feed mean
    # something here. They do not in a discussion post.
    resolves_directives = True

    STATUSES = ('draft', 'published', 'archived')
    PRESENTATIONS = ('site', 'community')

    @classmethod
    def unsaved_draft(cls, type_slug: str, stored: 'Content | None' = None):
        """A row built to be rendered once and thrown away.

        Previewing an edit must not touch what is stored, so the editor's
        form is read into one of these instead of into the live row. It is
        never added to a session, which is what makes that safe rather than
        merely careful. Columns the editor does not post are copied off the
        stored row, because the page prints them.
        """
        draft = cls(type=type_slug)
        if stored is not None:
            draft.status, draft.published_at = stored.status, stored.published_at
        return draft

    def attach_featured_upload(self):
        """Resolve featured_upload_id into the relationship by hand.

        A row that was never written cannot lazy-load one from an id, so a
        preview would otherwise show no picture at all.
        """
        from app.models import Upload
        self.featured_upload = (
            Upload.query.filter_by(id=self.featured_upload_id).first()
            if self.featured_upload_id else None)

    def validate(self):
        from app.platform.content_types import CONTENT_TYPES
        self.title = (self.title or '').strip()
        self.type = self.type or 'article'
        self.status = self.status or 'draft'
        self.visibility = self.visibility or 'public'
        # A blank slug is derived from the title rather than refused. Here
        # rather than in the form handler, so a seed, an import or anything
        # written later gets it too. An existing slug is never rewritten: it
        # is the item's public address, and a published one must not move
        # because somebody edited a typo in the title.
        #
        # After the type is settled, not before: uniqueness is per type, so
        # deriving first would look for a free address among rows whose type
        # is still None, find nothing taken, and hand back a slug that the
        # uniqueness check then refuses.
        self.slug = (self.slug or '').strip().lower() or None
        if not self.is_child:
            # Position means where inside my parent, and a row with no
            # parent has no inside. A promoted block would otherwise keep
            # the number it had in the course it came out of.
            self.position = None
        if self.is_child and not self._wants_an_address():
            # A block read only inside its parent has no address, so there
            # is nothing to derive and nothing to keep unique. Any slug that
            # came in with it is dropped rather than stored unused: a value
            # nothing reads is a value somebody will eventually believe.
            self.slug = None
        elif not self.slug:
            self.slug = self._free_slug(slugify(self.title))
            if not self.slug:
                raise ValidationError(
                    'Add a slug: the title has no letters or numbers to '
                    'make one from')

        if not self.title:
            raise ValidationError('Title is required')
        if len(self.title) > 200:
            raise ValidationError('Title too long (max 200 chars)')
        # This is the newsletter subject line (app/platform/newsletter.py).
        reject_control_characters(self.title, 'Title')
        # Columns declare these widths, but SQLite does not enforce them,
        # so an over-long value stores in development and raises in
        # production. The check has to live here.
        for field, label, limit in (('excerpt', 'Excerpt', 500),
                                    ('seo_title', 'SEO title', 200),
                                    ('seo_description', 'SEO description', 300),
                                    ('body', 'Body', BODY_MAX)):
            if len(getattr(self, field) or '') > limit:
                raise ValidationError(f'{label} too long (max {limit} chars)')
        if self.slug is not None and not re.fullmatch(
                r'[a-z0-9]([a-z0-9-]{0,198})?', self.slug):
            raise ValidationError('Slug must be lowercase letters, numbers, hyphens')
        if self.type not in CONTENT_TYPES:
            raise ValidationError(f'Unknown content type: {self.type}')
        if self.status not in self.STATUSES:
            raise ValidationError('Invalid status')
        from app.platform.authz import visibility_is_valid
        if not visibility_is_valid(self.visibility, self.org_id):
            raise ValidationError('Invalid visibility')
        self.presentation = self.presentation or 'site'
        if self.presentation not in self.PRESENTATIONS:
            raise ValidationError('Invalid presentation')
        if self.tags is not None and not isinstance(self.tags, list):
            raise ValidationError('Tags must be a list')

        # `template` names one of the theme's page templates. It reaches
        # render_site()'s candidate list, which also searches app-owned
        # directories, so an unchecked value renders an application
        # template on a public URL. This is the structural half: a name,
        # never a path. Whether the theme actually provides that name is
        # checked where the value is accepted, in manage._content_from_form,
        # so a value stranded by a later theme switch does not block
        # unrelated edits to the same page.
        if self.template is not None:
            self.template = self.template.strip()
            if not self.template:
                self.template = None
            elif not PAGE_TEMPLATE_RE.fullmatch(self.template):
                raise ValidationError(
                    'Template must be lowercase letters, numbers and '
                    'hyphens: a template name, not a path')

        if self.is_child:
            self._validate_as_child()
            if self.slug is None:
                return          # no address, so nothing left to check
        elif self.slug in RESERVED_ITEM_SLUGS:
            # Every standalone type, not only pages: an item of any type
            # sits at /<base>/<slug>, and that route is taken by the type's
            # own feed. A slug that collides does not lose a race -- a
            # static segment wins in the URL map wherever it is registered
            # -- it makes the item unreachable for good.
            #
            # A block is exempt because it is not there: a child lives at
            # /<base>/<slug>/<base>/<slug>, four segments deep, where none
            # of these are mounted.
            #
            # Unconditional, including on a row that somehow already
            # holds one. Being lenient about those was tried and was worse
            # than the problem: the check asked SQLAlchemy whether the slug
            # had changed, and anything earlier in this method that runs a
            # query autoflushes the pending value first, so the history it
            # read was already empty and the rule could be walked straight
            # past.
            #
            # Nothing is stranded by that strictness. This rule has been
            # here since the feeds were, so no row can have been written
            # around it, and every write goes through validate. A database
            # predating it would need one of these renamed by hand -- there
            # were none anywhere when this shipped.
            raise ValidationError('That slug is reserved')
        elif self.content_type.is_page:
            # Page slugs live at /<slug>, so they cannot shadow app routes
            # or a feed type's base segment (e.g. "blog", "events").
            # Every archive base, not just the tenant's active ones: a page
            # must not take a slug that a later plugin install would shadow.
            bases = {ct.base.strip('/') for ct in CONTENT_TYPES.values()
                     if ct.has_archive}
            if self.slug in RESERVED_PAGE_SLUGS or self.slug in bases:
                raise ValidationError('That slug is reserved')

        # Unique per (org, type, slug) for a standalone row -- a page
        # "about" and an article "about" coexist because they live at
        # different URLs -- and per (parent, type, slug) for a block, so two
        # courses can each have a lesson called "one".
        query = Content.query.filter_by(type=self.type, slug=self.slug,
                                        parent_id=self.parent_id)
        existing = scoped_to_own_org(query, self).first()
        if existing and existing.id != self.id:
            raise ValidationError(
                f'A {self.content_type.singular.lower()} with that slug already exists')

    def _wants_an_address(self) -> bool:
        """Does a block of this type get a URL under its parent?

        Asked without going through content_type, which assumes the type is
        registered: this runs before the type has been checked.
        """
        from app.platform.content_types import CONTENT_TYPES
        content_type = CONTENT_TYPES.get(self.type)
        return bool(content_type and content_type.child_routable)

    def _validate_as_child(self) -> None:
        """What has to be true of a block, checked where everything else
        about a content row is checked.

        One level, deliberately. A block inside a block inside a block is a
        layout tree, and the axiom this architecture is built on is that
        Supremely is not a site builder: blocks are content, never layout.
        One level also keeps rendering and routing finite -- a parent draws
        its children and that is the end of it.
        """
        parent = self.parent
        if parent is None:
            # Set by id without the object loaded, which is normal on a save.
            parent = Content.query.filter_by(id=self.parent_id).first()
        if parent is None:
            raise ValidationError('That block has no parent')
        if parent.id == self.id:
            raise ValidationError('An item cannot be inside itself')
        if parent.parent_id is not None:
            raise ValidationError(
                'Blocks go one level deep: this one is already inside '
                'something else')
        if self.children:
            raise ValidationError(
                'Blocks go one level deep: this one already has blocks of '
                'its own')
        if self.position is None:
            # Added blocks go on the end. Set here rather than in the editor
            # so a seed, an import or a plugin gets an order too, instead of
            # a pile of nulls that sort differently on each engine.
            taken = [sibling.position for sibling in parent.children
                     if sibling.id != self.id and sibling.position is not None]
            self.position = max(taken, default=-1) + 1

    @property
    def is_child(self) -> bool:
        return self.parent_id is not None

    def move_child(self, child_id: int, delta: int) -> None:
        """Move one block up or down inside this item.

        Positions are rewritten from the resulting order rather than swapped
        in place: rows written before positions existed, or by a seed or an
        import, can hold nulls and duplicates, and a swap between two of
        those moves nothing. Renumbering makes the order true whatever it
        started as. Asking to move the first block up is not an error, it
        just leaves the order alone.
        """
        blocks = list(self.children)
        index = next((i for i, block in enumerate(blocks)
                      if block.id == child_id), None)
        if index is None:
            raise ValidationError('That block is not in this item')
        target = index + delta
        if 0 <= target < len(blocks):
            blocks.insert(target, blocks.pop(index))
        for position, block in enumerate(blocks):
            block.position = position
        self.save()

    def visible_children(self) -> list['Content']:
        """The blocks of this item to list, for the current visitor.

        A child decides its own visibility, and that is the point rather
        than an oversight: lesson one of a course is public and the rest are
        members-only, which is a free preview for the price of a nullable
        column. Draft blocks are left out of a published parent the same way
        a draft article is left out of an archive.

        Listing follows the organization's tease-or-hide switch, the same
        one visible_query applies to archives, so a gated lesson is a locked
        title on the course page where the organization teases and is absent
        where it does not.

        Not gated on whether the organization still publishes the block's
        type, deliberately, and this is the one place that answer differs
        from published_query's. Turning a type off means it stops being
        published as a section: its archive goes, its listings go, its
        routes go. It does not mean deleting words out of the middle of an
        article somebody wrote. A block is part of its parent, and the
        parent is what was turned on. Deciding it separately here would have made a
        course the one place in the product where gating means "vanish"
        while everywhere else it means "locked title", and left the lesson's
        own gate page undiscoverable. The template still asks can_view about
        each one before it draws a body.
        """
        from flask import g

        org = getattr(g, 'org', None)
        listed = []
        for child in self.children:
            if not child.is_published:
                continue
            if child.visible_to_current_visitor():
                listed.append(child)
                continue
            # Gated. It may still be listed as a locked title, but only on
            # the same terms its own archive would list it on: its own
            # type's teasing switch, not the parent's, and never when that
            # type's whole section is locked. Asking the parent's type
            # meant a course could advertise a lesson that /lessons itself
            # refuses to name.
            if not Content.section_readable_by_current_visitor(child.type):
                continue
            if org is not None and org.type_teases(child.type):
                listed.append(child)
        return listed

    def _free_slug(self, base: str) -> str:
        """An address near `base` that is actually available.

        Only ever used for a slug the author did not type. A derived slug
        that collided would stop a save with an error about a field they
        never filled in, so a second post called "Hello" becomes hello-2
        rather than a refusal. A slug somebody typed is left exactly as
        typed and still collides loudly, because they asked for that one.
        """
        from flask import g, has_request_context

        from app.platform.content_types import CONTENT_TYPES
        if not base:
            return ''
        # Without a tenant to scope to, scoped_to_own_org would search every
        # organization's content. Skip the walk rather than let one
        # organization's slugs shape another's: the uniqueness constraint
        # still refuses a genuine clash.
        scoped = bool(self.org_id) or (has_request_context()
                                       and getattr(g, 'org', None) is not None)
        if not scoped:
            return base
        reserved = set(RESERVED_ITEM_SLUGS)
        if self.content_type.is_page:
            reserved |= RESERVED_PAGE_SLUGS | {
                ct.base.strip('/') for ct in CONTENT_TYPES.values()
                if ct.has_archive}

        def taken(candidate: str) -> bool:
            if candidate in reserved:
                return True
            # In the same address space this row lives in: a block only
            # has to be unique inside its parent, so a lesson called "one"
            # in another course is not a clash.
            clash = scoped_to_own_org(
                Content.query.filter_by(type=self.type, slug=candidate,
                                        parent_id=self.parent_id),
                self).first()
            return clash is not None and clash.id != self.id

        candidate, n = base, 1
        while taken(candidate):
            n += 1
            suffix = f'-{n}'
            candidate = f'{base[:DERIVED_SLUG_MAX - len(suffix)]}{suffix}'
            if n > 100:
                # Pathological, but a save must complete rather than spin.
                # Checked like any other candidate: an unchecked one would
                # surface as the collision error this method exists to
                # prevent, on a slug nobody typed.
                while taken(candidate):
                    candidate = (f'{base[:DERIVED_SLUG_MAX - 7]}-'
                                 f'{secrets.token_hex(3)}')
                break
        return candidate

    @property
    def content_type(self):
        from app.platform.content_types import get_content_type
        return get_content_type(self.type)

    @property
    def is_published(self) -> bool:
        return self.status == 'published'

    @property
    def category(self):
        """The item's category, or None.

        An item has one. The join table stays a many-to-many because that is
        what it already was and nothing is gained by rewriting it, but the
        editor offers a single choice, so callers that want "the" category --
        the card's chip, its colour -- have one thing to ask rather than an
        arbitrary first element.
        """
        return self.categories[0] if self.categories else None

    @property
    def discussion(self):
        """The thread discussing this item, or None.

        A reference, not ownership: the thread is an ordinary discussion post
        living in a group and moderated there (app/models/discussion.py).
        """
        from .discussion import Post
        return Post.for_content(self.id)

    @property
    def permalink(self) -> str:
        """Where this item is read.

        A block has no address of its own. One whose type wants pages gets
        one under its parent -- /courses/intro/lessons/one -- and one whose
        type does not is read inside its parent, so that is where a link to
        it goes. Never an empty href: a template that links a block should
        not have to know which kind it has.
        """
        ct = self.content_type
        if self.is_child:
            parent = self.parent
            if parent is None:
                return ''
            # A block's own address exists only where something serves it:
            # under a parent whose type has an archive base to hang it from,
            # and only while this organization still publishes the block's
            # type, because the route resolves that type before it answers.
            # A block whose address nothing serves is read inside its
            # parent, so that is the honest link -- better than a
            # tidy-looking 404 on every course page.
            from app.platform.content_types import type_is_active
            if (ct.child_routable and self.slug
                    and parent.content_type.has_archive
                    and type_is_active(ct)):
                return f'{parent.permalink}{ct.base}/{self.slug}'
            return parent.permalink
        if ct.is_page:
            return f'/{self.slug}'
        return f'{ct.base}/{self.slug}'

    def excerpt_or_summary(self, length: int = 200) -> str:
        """The short form of this item, for a listing or an email.

        Always derived from the body. The editor used to offer a separate
        excerpt and no longer does, so deriving it is the only way every
        item summarises the same way: otherwise an item written before the
        field was removed would keep showing copy nobody could edit, next
        to one that summarises itself, with nothing on screen to explain
        the difference. The column keeps whatever was written in it.
        """
        import nh3

        from app.platform.content import render_markdown
        # Only as much of the body as a summary could possibly need.
        #
        # A body may be half a megabyte and this returns a couple of hundred
        # characters of it, so rendering the whole thing and throwing nearly
        # all of it away is work every listing card pays for every item it
        # draws. An archive of twenty items was doing it twenty times.
        # Markdown never yields more text than its source, so a slice this
        # generous cannot come up short of the length asked for.
        #
        # Directives are dropped rather than resolved: a one-line summary
        # has no business pulling in whatever this body embeds, and
        # resolving cost a query and a template render apiece for text that
        # is then stripped of all its markup anyway. Pictures go the same
        # way and for the same reason -- one query each, for markup with no
        # text in it, down every card of an archive.
        body = self.body or ''
        source = body[:length * 20]
        text = nh3.clean(
            render_markdown(source, directives='drop', images=False),
            tags=set())
        text = ' '.join(text.split())
        if len(text) > length:
            return text[:length] + '…'
        return text + ('…' if len(body) > len(source) else '')

    def set_structured_fields(self, data: dict):
        """Write the type's declared fields, keeping anything else.

        A type can stop declaring a field, and the values organizations
        already typed into it do not stop existing. Replacing the whole bag
        with the cleaned declared fields would destroy them on the next
        unrelated save, so an author fixing a typo in a title would silently
        lose data. Same rule the excerpt follows: keep what was written,
        stop offering it.
        """
        kept = {key: value for key, value in (self.fields or {}).items()
                if key not in self.content_type.field_keys}
        self.fields = {**kept, **self.content_type.clean_fields(data)}
        return self

    def publish(self):
        self.status = 'published'
        if self.published_at is None:
            self.published_at = utcnow()
        return self.save()

    def unpublish(self):
        self.status = 'draft'
        return self.save()

    def archive(self):
        self.status = 'archived'
        return self.save()

    @classmethod
    def type_visibility(cls, type_slug: str) -> str:
        """Org-wide lock for a whole content section (Manage → Content
        types). Absent means public: items then decide individually.

        One map (Organization.TYPE_SETTINGS_KEY) holds everything an
        organization has said about a type. There is no fallback to the
        per-question map this replaced, deliberately: two places to read the
        same answer from is how the two come to disagree.
        """
        from flask import g
        org = getattr(g, 'org', None)
        if org is None:
            return 'public'
        return org.type_visibility(type_slug)

    @classmethod
    def section_readable_by_current_visitor(cls, type_slug: str) -> bool:
        from app.platform.authz import can_read
        return can_read(cls.type_visibility(type_slug))

    def visible_to_current_visitor(self) -> bool:
        # A locked section gates every item in it, item settings
        # notwithstanding (mirrors the discussions area switch). Both
        # questions are the same question, asked of two values.
        from app.platform.authz import can_read
        return (Content.section_readable_by_current_visitor(self.type)
                and can_read(self.visibility))

    # --- queries -----------------------------------------------------------

    @classmethod
    def standalone(cls):
        """Content that stands on its own, which is what a listing means.

        A block inside an article is a content row like any other, so every
        archive, feed, count, sitemap and search would list it next to the
        article it belongs to unless something says otherwise. This is that
        something, and it is one method rather than a parent_id test at each
        of the dozen readers, for the same reason the tenant filter is one
        filter: a listing added later gets the rule by starting here, and
        the twelfth caller cannot forget what the first eleven remembered.

        Deliberately not a global filter like the tenant one. Rendering a
        parent has to be able to load its children, and a rule with an
        exception is a rule every caller has to think about.
        """
        return cls.query.filter(cls.parent_id.is_(None))

    @classmethod
    def of_type(cls, type_slug: str):
        return cls.standalone().filter_by(type=type_slug)

    @classmethod
    def count_by_type(cls):
        """(type_slug, count) pairs for this org's content, tenant-scoped."""
        import sqlalchemy as sa

        from app.extensions import db
        return (db.session.query(cls.type, sa.func.count(cls.id))
                .filter(cls.parent_id.is_(None))
                .group_by(cls.type).all())

    @classmethod
    def published_query(cls, type_slug: str | None = None):
        """Published rows of a type this organization publishes.

        The type gate belongs here rather than at each reader. Routes were
        already gated by type_for_base, but a theme's front-page grid, the
        community rail's announcement and event cards, and the navigation
        editor's list of linkable pages all read content directly: a
        section turned off went on publishing in every one of them, which
        is not what turning it off means.

        Not in count_by_type, deliberately: the console has to say how many
        items are waiting inside a type that is off, or there is no way to
        judge whether to turn it back on.

        Order is the type's own answer where one type is asked for. A
        roster, a glossary and a recipe index are not timelines, and a theme
        sorting them back into shape in Jinja is a theme working around the
        model. Asked for everything at once there is no single type to ask,
        so newest wins.
        """
        from app.platform.content_types import active_types
        active = active_types()
        q = cls.standalone().filter_by(status='published')
        if type_slug:
            if type_slug not in active:
                return q.filter(sa.false())
            q = q.filter_by(type=type_slug)
            return q.order_by(*cls.order_for(active[type_slug]))
        # Everything at once: no single type to ask, so newest wins. Still
        # tiebroken, because a mixed feed reshuffling between page loads is
        # no better than a single type's archive doing it.
        q = q.filter(cls.type.in_(list(active)))
        return q.order_by(*cls.order_for())

    @classmethod
    def order_for(cls, content_type: 'ContentType | None' = None) -> tuple:
        """The columns a listing reads in.

        Alphabetical folds case in the query rather than leaning on the
        database's own idea of alphabetical. SQLite compares bytes, so every
        capital letter sorts before every lowercase one and "Zoe" comes
        before "adam"; PostgreSQL compares by locale and does not. Without
        the fold, the same roster reads in a different order depending on
        which engine an installation happens to run, which is exactly what
        an installation should never have to think about.

        Every ordering ends in a tiebreak on id, so two items sharing a
        title, a date or a position come back in the same order on every
        request and on both engines. A list that quietly reshuffles between
        page loads is worse than one in the wrong order.
        """
        ordering = content_type.ordering if content_type is not None else ''
        # Nulls last, spelled out rather than left to the engine. SQLite and
        # PostgreSQL disagree about where a null sorts, and every column
        # ordered on here can hold one: a draft has no published_at, and an
        # item nobody arranged has no position. Without this the console
        # list of a type with drafts in it reads in a different order
        # depending on which database an installation runs.
        def nulls_last(column):
            return sa.case((column.is_(None), 1), else_=0)

        by = {
            'oldest': (nulls_last(cls.published_at), cls.published_at.asc()),
            'alphabetical': (sa.func.lower(cls.title).asc(),),
            'manual': (nulls_last(cls.position), cls.position.asc(),
                       nulls_last(cls.published_at), cls.published_at.desc()),
        }.get(ordering, (nulls_last(cls.published_at),
                         cls.published_at.desc()))
        return (*by, cls.id.desc())

    @classmethod
    def visible_query(cls, type_slug: str | None = None):
        """Published content the current visitor may see listed.

        The tease-don't-hide switch decides what "listed" means: on (the
        default) a gated item stays in the list as a locked title, and the
        template draws the padlock from can_view; off, it is filtered out
        here. One path, so a theme's grid and an archive page agree.
        """
        from flask import g

        from app.platform.authz import readable_visibilities
        query = cls.published_query(type_slug)
        org = getattr(g, 'org', None)
        if org and org.type_teases(type_slug):
            return query
        readable = readable_visibilities()
        if readable is None:            # reads everything, tiers included
            return query
        return query.filter(cls.visibility.in_(readable))

    @classmethod
    def syndication_query(cls, type_slug: str | None = None) -> 'Query':
        """What a feed carries: an archive's listing, newest first.

        Here rather than in the controller that renders XML, because
        "which items may this reader be shown" is the same question an
        archive asks and must not have two answers. It had two for one
        review cycle, and the feed's was wrong: asked for everything at
        once it filtered each item's own visibility and never the
        organization's lock on a whole section, so a site that had closed
        its blog entirely still listed every article's title and address
        to anybody who asked for /feed.

        A section the reader may not enter contributes nothing, whatever
        its items say. Within the sections left, visible_query decides,
        so the tease-don't-hide switch is answered in one place for the
        page and the feed alike.

        Newest first whatever order the type declares for its archive: a
        roster may read alphabetically on the page, but a feed is a
        timeline and every reader sorts it by date regardless.

        Every type is asked separately even when the feed covers all of
        them, because teasing is a per-type answer: an organization may
        advertise its articles and say nothing at all about its jobs
        board. visible_query(None) has no type to put that question to and
        falls back to the organization-wide default, which is right for
        neither type that overrides it.

        Eager-loaded, because a feed renders the author and the categories
        of every item it carries and this is a public endpoint that
        machines poll.
        """
        from sqlalchemy.orm import joinedload, selectinload

        from app.platform.content_types import feed_types
        readable = [ct.slug for ct in feed_types()
                    if cls.section_readable_by_current_visitor(ct.slug)]
        if type_slug is not None:
            if type_slug not in readable:
                return cls.query.filter(sa.false())
            query = cls.visible_query(type_slug)
        elif not readable:
            return cls.query.filter(sa.false())
        else:
            query = cls.published_query().filter(
                sa.or_(*[cls._listable_clause(slug) for slug in readable]))
        return (query.order_by(None)
                .order_by(cls.published_at.desc(), cls.id.desc())
                .options(joinedload(cls.created_by),
                         selectinload(cls.categories)))

    @classmethod
    def _listable_clause(cls, type_slug: str) -> 'sa.ColumnElement[bool]':
        """The rows of one type this visitor may see listed, as a clause.

        The same rule visible_query applies, expressed so several types can
        be ORed into one statement: teased, every published row of the type
        counts; not teased, only the visibilities this reader may read.
        """
        from flask import g

        from app.platform.authz import readable_visibilities
        readable = readable_visibilities()
        org = getattr(g, 'org', None)
        if readable is None or (org and org.type_teases(type_slug)):
            return cls.type == type_slug
        return sa.and_(cls.type == type_slug,
                       cls.visibility.in_(readable))

    @classmethod
    def public_archive_types(cls) -> list[str]:
        """Type slugs whose whole archive is a public address.

        The gate the sitemap needs for the archive URL itself, and the one
        public_query applies to the items underneath it. One answer, so
        the file cannot advertise a section whose archive would meet a
        crawler with a gate page.
        """
        from app.platform.content_types import feed_types
        return [ct.slug for ct in feed_types()
                if cls.type_visibility(ct.slug) == 'public']

    @classmethod
    def public_pages(cls, limit: int = 1000) -> list['Content']:
        """Published pages anybody may read, for the sitemap.

        Pages have no archive, so public_query's walk over feed_types
        never reaches them, and a sitemap without them leaves out /about
        and every other standalone address the site has.
        """
        if cls.type_visibility('page') != 'public':
            # The same gate every other section gets. Pages have no
            # archive, so nothing else here would ever ask.
            return []
        return (cls.published_query('page')
                .filter(cls.visibility == 'public',
                        cls.parent_id.is_(None))
                .order_by(None).order_by(cls.updated_at.desc())
                .limit(limit).all())

    @classmethod
    def public_query(cls, type_slug: str) -> 'Query':
        """Published content anybody may read, whoever is asking.

        For the sitemap, which is built for crawlers. visible_query answers
        for the visitor in front of it, and a sitemap built that way would
        put members-only addresses into a file whose whole purpose is to be
        fetched by strangers and cached by them.

        A locked section is skipped whole: its archive would answer a
        crawler with a gate page, so its items have no public address to
        advertise either.

        One type at a time, because the sitemap walks them to put each
        archive's own address in beside its items. An all-types branch was
        written first and never had a caller.
        """
        from sqlalchemy.orm import selectinload

        if type_slug not in cls.public_archive_types():
            return cls.query.filter(sa.false())
        query = cls.published_query(type_slug)
        return (query.filter(cls.visibility == 'public')
                .order_by(None)
                .order_by(cls.published_at.desc(), cls.id.desc())
                .options(selectinload(cls.categories)))

    # A theme asks for "recent articles" without saying how many; this is how
    # many it gets, and the ceiling on how many it can ask for. A front page
    # renders a grid, not an archive.
    FEED_LIMIT = 24

    @classmethod
    def feed(cls, type_slug: str, limit: int | None = None) -> list['Content']:
        """Published items of one type for a theme template, in the order
        that type declares.

        Empty is normal: an unregistered type, a locked section or a site
        with nothing published all return [], never an error. The featured
        image and author are loaded with the rows, so iterating the result
        cannot turn into an N+1.
        """
        from sqlalchemy.orm import joinedload
        if not cls.section_readable_by_current_visitor(type_slug):
            return []
        if limit is None:
            limit = cls.FEED_LIMIT
        else:
            try:
                limit = min(int(limit), cls.FEED_LIMIT)
            except (TypeError, ValueError):
                limit = cls.FEED_LIMIT      # a theme's typo is not an error
        if limit < 1:
            return []
        return (cls.visible_query(type_slug)
                .options(joinedload(cls.featured_upload),
                         joinedload(cls.created_by))
                .limit(limit).all())

    @classmethod
    def feed_count(cls, type_slug: str) -> int:
        """How many items `feed()` is drawing from, for "view all" links."""
        if not cls.section_readable_by_current_visitor(type_slug):
            return 0
        return cls.visible_query(type_slug).count()

    def can_open_editor(self) -> bool:
        """Whether the current viewer may open this item in the editor.

        Two things have to be true. The item must have been written down,
        because the editor is addressed by id and the preview renders a
        throwaway row that was never saved. And the viewer must hold
        content.write, which is what the console route asks for: offering
        an Edit button to somebody the editor would turn away is worse
        than offering none.

        Named apart from the can_edit() a discussion post carries, which
        answers a different question: there authorship grants the right,
        because a member writes their own posts. Here it grants nothing.
        Nobody without content.write can author an item at all, so an
        author since demoted to member gets no button, and the editor
        would have refused them anyway.

        Authorship decides how the button is drawn rather than whether it
        appears at all -- see authored_by_viewer.
        """
        from flask import has_request_context

        from app.platform.authz import can
        if not has_request_context():
            return False          # no viewer to answer for: fail closed
        return bool(self.id) and can('content.write')

    def authored_by_viewer(self) -> bool:
        """Whether the current viewer wrote this.

        Editing your own article is an ordinary thing to do. Editing
        somebody else's is an administrator reaching into their work, and
        the button says which one is happening. Content that nobody is
        recorded as having written, such as the items provisioning seeds,
        counts as somebody else's.
        """
        from flask import has_request_context
        from flask_login import current_user
        if not has_request_context():
            return False
        # Content nobody is recorded as having written compares False
        # here on its own, which is the answer wanted: a seeded item is
        # not the reader's work.
        return bool(current_user.is_authenticated
                    and self.created_by_id == current_user.id)

    @property
    def starts_on(self) -> str | None:
        """The date this item happens on, as an ISO string, or None.

        `fields` is a JSON blob and its values are whatever a seeder, an
        importer or a plugin wrote, so a value that is not a date-shaped
        string is treated as no date at all rather than compared and
        raising.
        """
        value = (self.fields or {}).get('starts_on')
        return value if isinstance(value, str) and value else None

    def has_happened(self) -> bool:
        """Whether this item's date is in the past.

        Something with no date has not happened and never will, which is
        what keeps the button on an undated item rather than hiding it.
        """
        from datetime import date
        return bool(self.starts_on
                    and self.starts_on < date.today().isoformat())

    def is_upcoming(self) -> bool:
        """Whether this item is dated today or later. Not the negation of
        has_happened: something with no date is neither."""
        from datetime import date
        return bool(self.starts_on
                    and self.starts_on >= date.today().isoformat())

    @classmethod
    def upcoming_event(cls) -> 'Content | None':
        """The next published event the current visitor may read, dated
        today or later. Event dates live in the structured `fields` JSON,
        so the (few) events are filtered in Python.

        No parameter. It took one called public_only, which the only caller
        passed as "is this visitor not a member": true enough while any
        member could read anything members-only, and wrong the moment a
        tier could sit between two members. An administrator is unfiltered
        here the same way they are everywhere else, because
        readable_visibilities answers None for them.
        """
        from app.platform.authz import can_read, readable_visibilities
        if not can_read(cls.type_visibility('event')):
            return None
        query = cls.published_query('event')
        readable = readable_visibilities()
        if readable is not None:
            query = query.filter(cls.visibility.in_(readable))
        events = [event for event in query.limit(50).all()
                  if event.is_upcoming()]
        # Keyed on the date alone. Comparing the pairs let a tie fall
        # through to comparing two rows, which raises, so two events on
        # one day took the community rail down with them.
        return min(events, key=lambda event: event.starts_on, default=None)

    @classmethod
    def published_by_slug(cls, type_slug: str, slug: str):
        return cls.standalone().filter_by(
            type=type_slug, status='published',
            slug=(slug or '').strip().lower()).first()

    @classmethod
    def published_page(cls, slug: str):
        return cls.published_by_slug('page', slug)

    @classmethod
    def with_tag(cls, type_slug: str, tag: str):
        import sqlalchemy as sa
        needle = json.dumps(tag)[1:-1]
        return (cls.visible_query(type_slug)
                .filter(sa.cast(cls.tags, sa.String)
                        .like(f'%"{escape_like(needle)}"%',
                              escape=LIKE_ESCAPE)))

    @classmethod
    def visible_in_category(cls, type_slug: str, category):
        return (cls.visible_query(type_slug)
                .filter(cls.categories.contains(category)))
