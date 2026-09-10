"""Who is coming to an event.

One row per person per event, and the row's existence is the answer: there
is no status column because there is one thing to say. Somebody who
changes their mind presses the button again and the row goes.

Deliberately not shaped like Reaction, which carries target_type and
target_id because it answers about two different things. An RSVP is only
ever about a content row, so the column says so and the database keeps it
honest: an event cannot be deleted out from under its guest list.
"""

from app.extensions import db

from .base import BaseModel, OrgScoped
from .types import BigIntFK


class Rsvp(OrgScoped, BaseModel):
    __tablename__ = 'rsvp'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    content_id = db.Column(BigIntFK,
                           db.ForeignKey('content.id', ondelete='CASCADE'),
                           nullable=False, index=True)

    __table_args__ = (
        # One answer per person per event. Two clicks racing each other
        # both try to insert, and the second is refused by the database
        # rather than leaving somebody counted twice.
        db.UniqueConstraint('user_id', 'content_id', name='uq_rsvp_user_content'),
    )

    @classmethod
    def toggle(cls, user_id: int, content_id: int) -> bool:
        """Say you are coming, or take it back. True if you are now going.

        No org_id: the tenant stamp fills it from the organization in
        force and refuses a row belonging anywhere else, so passing one
        could only ever repeat what it already knows or raise.

        Check-then-insert, so two presses racing each other both miss the
        existing row and the second insert meets the unique constraint.
        That is the guarantee; the caller catches what it raises.
        """
        existing = cls.query.filter_by(user_id=user_id,
                                       content_id=content_id).first()
        if existing:
            existing.delete()
            return False
        cls(user_id=user_id, content_id=content_id).save()
        return True

    @classmethod
    def is_going(cls, user_id: int, content_id: int) -> bool:
        return cls.query.filter_by(user_id=user_id,
                                   content_id=content_id).first() is not None

    @classmethod
    def count_for(cls, content_id: int) -> int:
        """How many are coming. Shown to everybody who can see the event,
        members and visitors alike: a number is not member data."""
        return cls.query.filter_by(content_id=content_id).count()

    @classmethod
    def attendees(cls, content_id: int, limit: int = 12) -> list:
        """The people coming, newest answer first, for the avatar row.

        Names and faces are member data, so the caller decides whether to
        ask: a visitor gets the count and nothing else, which is the line
        the member directory already draws.
        """
        from .user import User
        return (User.query.join(cls, cls.user_id == User.id)
                .filter(cls.content_id == content_id)
                .order_by(cls.created_at.desc()).limit(limit).all())
