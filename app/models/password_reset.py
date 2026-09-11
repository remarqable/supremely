"""One-time password reset links, for an installation with no email.

Email is optional in Supremely, so a member who forgets their password on an
installation without SMTP had no way back in: the only recovery was the
operator running `flask users reset-password` on the server. An owner or
admin can now hand them a link instead, through whatever channel they
already talk on.

Only a hash of the token is stored, for the reason the invitation model
gives: the database must never be a list of credentials. This one is
stronger than an invitation, which buys entry to one organization -- a
reset buys somebody's whole account -- so it expires in a day rather than a
week, only one can be outstanding at a time, and who may be handed one is a
rule rather than a convention. See can_be_reset_by_org.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING

from app.extensions import db
from app.platform.errors import ValidationError

from .base import AuditMixin, BaseModel, OrgScoped, transaction, utcnow
from .types import BigIntFK, TZDateTime

if TYPE_CHECKING:
    from .user import User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def can_be_reset_by_org(user: 'User | None', org_id: int) -> bool:
    """Whether this organization may reset this account's password.

    A password is not organization property. It opens the account
    everywhere the account goes, so handing an organization the power to
    change one is handing it everything else that account can reach. Two
    cases where that is more than the organization is owed:

    A platform administrator runs the whole installation, including every
    other organization on it. An organization's admin resetting that
    password would be taking the installation.

    Somebody who is also a member somewhere else brings the same problem a
    size down: a plain member here may be the owner of the organization
    next door, and a reset issued here would be a way to take that one.
    The check is deliberately "any other membership at all" rather than
    "any other membership that outranks this one", because roles move and
    a rule that depends on somebody's role elsewhere is a rule that
    silently stops holding when they are promoted.

    A deactivated account is refused too, and so is a suspended
    membership. A reset link is a way in, and letting one be issued for an
    account somebody has switched off would make this the way to switch it
    back on -- which holds at the organization's own level as much as at
    the installation's.

    One answer for all of them, deliberately. Told apart, the refusal
    would say which of the three it was, and two of them are facts about
    the rest of the installation rather than about this organization. Run
    together it does not distinguish them, which is all it can do: an
    organizer only ever sees this for somebody who is already their member,
    so they still learn that one of the three holds. Narrowing it to that
    is the point; it does not disappear.

    Nobody is stranded: `flask users reset-password` still works, and it
    belongs to whoever runs the server, which is who should be answering
    for any of these.

    A boolean rather than a refusal with wording in it, because the wording
    is the controller's. Models here raise plain English and do not reach
    for the request, and `t()` reads the language off it.
    """
    if user is None:
        return False
    if user.is_platform_admin or not user.is_active:
        return False
    if not _member_here(user.id, org_id):
        return False
    return not _belongs_elsewhere(user.id, org_id)


def _membership_in(user_id: int | None, org_id: int):
    """This account's active membership of one organization, or None.

    Named by org_id rather than left to the tenant filter, because
    Membership is not OrgScoped -- see _belongs_elsewhere.
    """
    if user_id is None:
        return None
    from .membership import Membership
    return Membership.query.filter_by(user_id=user_id, org_id=org_id,
                                      is_active=True).first()


def _member_here(user_id: int, org_id: int) -> bool:
    return _membership_in(user_id, org_id) is not None


def _may_still_issue(user_id: int | None, org_id: int) -> bool:
    """Whether whoever issued a link could issue it again today.

    An admin who has been demoted, suspended or removed keeps whatever
    links they made otherwise, which turns "take away their access" into
    something that does not take away their access. Asked as a permission
    rather than a role, so a moderator role added later needs no change
    here.

    An issuer whose account has since been deleted leaves created_by_id
    null (the column is SET NULL), and a link nobody is accountable for is
    one nobody should be able to redeem.
    """
    from app.platform.authz import ROLE_PERMISSIONS
    membership = _membership_in(user_id, org_id)
    if membership is None:
        return False
    return 'members.manage' in ROLE_PERMISSIONS.get(membership.role, set())


def _belongs_elsewhere(user_id: int, org_id: int) -> bool:
    """Whether this account is a member of any other organization.

    Membership is a plain BaseModel rather than OrgScoped -- it is the
    table that says which organization a person belongs to, so it cannot
    be filtered by the organization in force -- which means this query is
    already global and the org_id comparison has to be written out. That
    is the same reason _own_membership in the manage controller compares
    org_id by hand.

    Returns a boolean and never a row: what is being asked is whether
    there is something outside this organization, not what it is.
    """
    from .membership import Membership
    return bool(db.session.query(
        Membership.query
        .filter(Membership.user_id == user_id,
                Membership.org_id != org_id)
        .exists()).scalar())


class PasswordReset(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'password_reset'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    expires_at = db.Column(TZDateTime, nullable=False)
    used_at = db.Column(TZDateTime, nullable=True)
    # The credential this link was issued to replace, as the same digest a
    # session id carries. A link is a way past a password, so it must not
    # outlive the password it was made for: change that password by any
    # route -- the member choosing a new one, another link, the operator
    # running `flask users reset-password` -- and this no longer matches
    # and the link is dead. Without it, the command this model's own
    # docstring offers as the answer to a stolen link did not take it
    # away.
    stamp = db.Column(db.String(64), nullable=False)

    # foreign_keys spelled out: AuditMixin puts two more columns on this
    # table pointing at user, so which one this relationship follows is
    # not something SQLAlchemy can work out on its own.
    user = db.relationship('User', foreign_keys=[user_id], lazy='select')

    # A day. An invitation lasts a week because it is handed out ahead of
    # time and buys entry to one organization; this is handed to somebody
    # who is locked out right now and buys their account, so it should not
    # be lying around in a chat log a week later.
    EXPIRY_HOURS = 24

    @classmethod
    def issue(cls, user: 'User', org_id: int) -> tuple['PasswordReset', str]:
        """Returns (reset, token). The token is shown once, never stored.

        Any link already outstanding for this account stops working. Two
        live links is two chances for the wrong person to be holding one,
        and an admin who reissues is usually reissuing because the first
        one went astray.
        """
        if not can_be_reset_by_org(user, org_id):
            # The caller is expected to have asked already and said so in
            # its own words. This is the backstop, so it does not need any.
            raise ValidationError('This account cannot be reset by this '
                                  'organization')
        token = secrets.token_urlsafe(32)
        reset = cls(org_id=org_id, user_id=user.id,
                    token_hash=_hash_token(token),
                    stamp=user.session_auth_stamp(),
                    expires_at=utcnow() + timedelta(hours=cls.EXPIRY_HOURS))
        reset.stamp_audit()
        with transaction():
            # Retiring the old links and creating the new one are one
            # write. Committed separately, a failure in the middle leaves
            # the account with its previous link dead and no replacement,
            # which is the one state this is meant to prevent.
            cls._retire_outstanding(user.id)
            # No validate() to call: this model has none, because nothing
            # on the row comes from anybody typing. The id, the digest and
            # both timestamps are made here.
            db.session.add(reset)
        return reset, token

    @classmethod
    def _retire_outstanding(cls, user_id: int) -> None:
        """Spend every unused link this account has, without redeeming one.

        Stages rather than commits: the caller owns the transaction. Marked
        used rather than deleted, so the row that recorded who issued a
        link survives it being superseded.
        """
        for reset in cls.query.filter_by(user_id=user_id, used_at=None).all():
            reset.used_at = utcnow()
            db.session.add(reset)

    @classmethod
    def find_valid(cls, token: str) -> 'PasswordReset | None':
        """The unspent, unexpired reset this token names, or None.

        Scoped like every other query, so a link issued by one
        organization is not redeemable on another's address even though
        the account behind it is the same account.
        """
        if not token:
            return None
        reset = cls.query.filter_by(token_hash=_hash_token(token)).first()
        if reset is None or reset.used_at is not None:
            return None
        if utcnow() > reset.expires_at:
            return None
        # Everything below is asked again at redemption rather than only
        # at issue, because a day passes in between and a link that cannot
        # be taken away is a link that outlives every reason it existed.
        #
        # The account's own rules first: a membership elsewhere, a
        # deactivation or a promotion to platform admin can all arrive in
        # that day.
        if not can_be_reset_by_org(reset.user, reset.org_id):
            return None
        # Then the password it was issued against. Any change to it spends
        # this link, which is what makes a stolen one recoverable: the
        # member, or the operator at a shell, sets a new password and every
        # outstanding link stops working.
        if not hmac.compare_digest(reset.stamp or '',
                                   reset.user.session_auth_stamp()):
            return None
        # Then whoever issued it. An admin demoted, suspended or removed
        # since pressing the button keeps no way in. (That the member is
        # still an active member here is part of can_be_reset_by_org
        # above, so it is already answered.)
        if not _may_still_issue(reset.created_by_id, reset.org_id):
            return None
        return reset

    def redeem(self, password: str) -> None:
        """Set the new password and spend the link.

        The password is set first so a refused one (too short, too common)
        leaves the link usable and the person can try again. Setting it
        changes the digest every session id carries, so every session and
        remember cookie the account had is void -- which is the point when
        the reason for the reset is that somebody else may have been in it.
        """
        import sqlalchemy as sa
        with transaction():
            # One transaction, because half of this is worse than neither:
            # a failure between the two used to leave the password changed
            # and the one-time link still live. Staged rather than saved,
            # because save() commits and would end the transaction after
            # the first of them -- but validate() is still called, so
            # stepping around save() does not step around the model's own
            # checks.
            self.user.set_password(password)
            self.user.validate()
            db.session.add(self.user)
            # Spent with a condition rather than by assignment, so two
            # requests arriving with the same token cannot both find it
            # unspent and both set a password. The loser changes no rows
            # and is told the link is gone.
            #
            # unscoped() with org_id written out, which is what the tenant
            # guard asks for: it refuses every bulk UPDATE on an OrgScoped
            # model, because the filter it installs only rewrites selects
            # and so cannot scope this one. Same shape and same reasoning
            # as Notification.mark_all_read. Both id and org_id are pinned,
            # so the statement can reach exactly this row.
            from app.platform.tenant import unscoped
            with unscoped():
                spent = db.session.execute(
                    sa.update(type(self))
                    .where(type(self).id == self.id,
                           type(self).org_id == self.org_id,
                           type(self).used_at.is_(None))
                    .values(used_at=utcnow()))
            if spent.rowcount != 1:
                raise ValidationError('That link has already been used')

    def url(self, token: str) -> str:
        from app.platform.tenant import org_url

        from .organization import Organization
        return org_url(Organization.get_by_id(self.org_id), f'/reset/{token}')
