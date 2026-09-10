"""Membership tiers: a named, ordered access level inside one organization.

A tier answers what a member may read, and nothing else. What a member may
do is their role (app/platform/authz.py), and the two are deliberately
independent: an owner on the bottom tier still publishes, and a member on
the top tier still cannot.

The ladder is ordered by rank, lowest first, and content names the lowest
tier that may read it. Anyone at or above passes. Every organization has at
least one tier and every membership sits on one, so there is no second
meaning for "the bottom" and no null branch in the access check.

Tiers are retired, never deleted. Deleting one would orphan the content that
requires it and silently widen who can read that content; retiring hides it
from every picker and leaves both alone.
"""

import re

from app.extensions import db
from app.platform.authz import forget_tier_ranks
from app.platform.errors import ValidationError

from .base import AuditMixin, BaseModel, OrgScoped, scoped_to_own_org

# `tier:` plus this fits inside the visibility column, which is what carries
# a tier requirement (see app/platform/authz.py).
SLUG_MAX = 30


class Tier(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'tier'

    name = db.Column(db.String(50), nullable=False)
    # Written once, at creation. A rename is free; changing the slug would
    # be a rewrite of every row whose visibility points at it.
    slug = db.Column(db.String(SLUG_MAX), nullable=False)
    # 1 is the bottom. Not unique, matching DiscussionGroup.position: every
    # mutation renumbers the ladder, so a duplicate cannot survive a write,
    # and reads order by (rank, id) so a transient tie is still settled.
    rank = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'slug', name='uq_tier_org_slug'),
    )

    # What provisioning gives a new community: one tier, so a community that
    # never thinks about tiers sees a single harmless row.
    DEFAULT_NAME = 'Free'
    DEFAULT_SLUG = 'free'

    def validate(self) -> None:
        self.name = (self.name or '').strip()
        self.slug = (self.slug or '').strip().lower()
        if not self.name:
            raise ValidationError('Tier name is required')
        if len(self.name) > 50:
            raise ValidationError('Tier name too long (max 50 chars)')
        if not re.fullmatch(rf'[a-z0-9]([a-z0-9-]{{0,{SLUG_MAX - 1}}})?',
                            self.slug):
            raise ValidationError(
                'Slug must be lowercase letters, numbers, hyphens')
        existing = scoped_to_own_org(
            Tier.query.filter_by(slug=self.slug), self).first()
        if existing and existing.id != self.id:
            raise ValidationError('A tier with that slug already exists')

    # --- reading the ladder ------------------------------------------------

    @classmethod
    def in_order(cls, include_retired: bool = False) -> list['Tier']:
        """This organization's tiers, bottom first.

        Retired tiers are left out by default: they are not offered as a
        choice anywhere. They are still readable by slug, because content
        that requires one still means what it meant.
        """
        query = cls.query if include_retired else cls.query.filter_by(
            is_active=True)
        return query.order_by(cls.rank, cls.id).all()

    @classmethod
    def by_slug(cls, slug: str) -> 'Tier | None':
        """One tier by slug, retired ones included."""
        return cls.query.filter_by(slug=(slug or '').strip().lower()).first()

    @classmethod
    def bottom(cls, org_id: int) -> 'Tier | None':
        """The lowest active tier of one organization, which is where a new
        member lands.

        Named organization rather than the one in force, and read through
        unscoped(), because the caller that adds a member may be looking at
        a different organization than the one it is adding to: the platform
        console adds somebody while resolving no organization at all. The
        filter would find no tier there and the caller would go on to
        create a second Free beside the first.

        Only the read crosses tenants. Creating a tier for an organization
        while another is in force is refused by the write guard, and should
        be: that is a caller doing something it has not said it means.
        """
        from app.platform.tenant import unscoped
        with unscoped():
            return (cls.query.filter_by(org_id=org_id, is_active=True)
                    .order_by(cls.rank, cls.id).first())

    @classmethod
    def ensure_default(cls, org_id: int,
                       created_by_id: int | None = None) -> 'Tier':
        """The organization's bottom tier, created if it has none.

        Provisioning calls this before the owner's membership exists,
        because a membership has to point at a tier. Existing organizations
        met it in the migration; this is the belt to that migration's
        braces, and it is what makes a seeded or hand-built organization
        work without a special case.

        `created_by_id` is passed rather than stamped, because provisioning
        runs before anybody is signed in as a member of the organization
        being created.
        """
        from app.platform.tenant import unscoped
        existing = cls.bottom(org_id)
        if existing is not None:
            return existing
        with unscoped():
            return cls(org_id=org_id, name=cls.DEFAULT_NAME,
                       slug=cls.DEFAULT_SLUG, rank=1,
                       created_by_id=created_by_id).save()

    @classmethod
    def member_counts(cls, org_id: int) -> dict[int, int]:
        """{tier_id: how many memberships sit on it}, for one organization.

        The Tiers page shows this beside Retire, because retiring a tier
        people hold is a different decision from retiring an empty one. One
        query for the whole ladder rather than one per rung, and pinned to
        the organization by hand because Membership is deliberately not
        OrgScoped and the filter never sees it.

        Suspended memberships count: they are still on the rung, and a
        suspension is a separate thing from a tier.
        """
        from .membership import Membership
        rows = (db.session.query(Membership.tier_id, db.func.count())
                .filter(Membership.org_id == org_id)
                .group_by(Membership.tier_id).all())
        return dict(rows)

    # --- changing the ladder -----------------------------------------------

    @classmethod
    def add(cls, name: str, slug: str) -> 'Tier':
        """A new tier at the top of the ladder.

        The top, because a tier added to an existing community is nearly
        always a new thing being offered above what is already there, and
        because appending cannot change what any existing content means.
        """
        from app.platform.tenant import current_org_id
        org_id = current_org_id()
        if org_id is None:
            raise ValidationError('No organization')
        highest = (cls.query.filter_by(org_id=org_id)
                   .order_by(cls.rank.desc(), cls.id.desc()).first())
        tier = cls(org_id=org_id, name=name, slug=slug,
                   rank=(highest.rank + 1) if highest else 1)
        tier.stamp_audit()
        tier.save()
        # After the insert, not before: a lookup between the two would
        # refill the memo from the ladder this tier is not in yet, and a
        # refused save (a duplicate slug) would have dropped a memo for a
        # change that never happened.
        forget_tier_ranks()
        return tier

    def move(self, direction: int) -> 'Tier':
        """Swap places with the neighbour below (-1) or above (+1).

        The whole ladder is renumbered 1..n in its current order first, the
        same way DiscussionGroup.move does, so a move always changes the
        order rather than only a hidden tiebreaker. Retired tiers move with
        it: they keep a rank so that content requiring one still compares.
        """
        tiers = self.in_order(include_retired=True)
        ids = [tier.id for tier in tiers]
        if self.id not in ids:
            return self             # not one of this tenant's tiers
        for index, tier in enumerate(tiers, start=1):
            tier.rank = index
        here = ids.index(self.id)
        there = here + (-1 if direction < 0 else 1)
        if 0 <= there < len(tiers):
            neighbour = tiers[there]
            self.rank, neighbour.rank = neighbour.rank, self.rank
        self.stamp_audit()
        db.session.commit()
        forget_tier_ranks()
        return self

    def rename(self, name: str) -> 'Tier':
        """A rename is free. The slug is not renamed, because it is what
        every gated row points at."""
        self.name = name
        self.stamp_audit()
        return self.save()

    def retire(self) -> 'Tier':
        """Take this tier out of circulation without deleting it.

        Members on it keep it and keep their rank. Content that requires it
        still resolves. It simply stops being offered.
        """
        self.organization_must_keep_a_tier()
        forget_tier_ranks()
        self.is_active = False
        self.stamp_audit()
        return self.save()

    def restore(self) -> 'Tier':
        forget_tier_ranks()
        self.is_active = True
        self.stamp_audit()
        return self.save()

    def organization_must_keep_a_tier(self) -> None:
        """An organization must always have at least one active tier, the
        same way it must always have at least one owner. Without one a new
        member has nowhere to land.

        Pinned to this row's organization rather than trusting the session
        filter, which stands down outside a request: from the CLI, a job or
        a seed an unpinned count would total every tenant's tiers and let
        an organization retire its last one.
        """
        active = scoped_to_own_org(
            Tier.query.filter_by(is_active=True), self).count()
        if active <= 1:
            raise ValidationError('An organization must keep at least one tier')
