"""Membership tiers

A community could say who may read a thing in two words: everyone, or
members. There was no way to say "the people who support us", and no way to
offer a second level of membership at all.

A tier is a named rung on a ladder inside one organization. Content names
the lowest tier that may read it and anyone at or above passes. Every
organization gets one tier, Free, and every membership sits on it, so a
community that never thinks about tiers has a single harmless row and
behaves exactly as it did before.

Three things this migration has to get right.

Every membership must end up on a tier, and the column is not nullable, so
it cannot simply be added. It arrives nullable, every row is filled in with
its own organization's Free tier, and only then does it become required.
That last step rebuilds the membership table on SQLite, which is why it runs
inside batch_alter_table.

A tier belongs to one organization. Two communities may both have a tier
called Pro and they are unrelated rows, which is what the unique constraint
on (org_id, slug) says.

The visibility columns were ten characters wide, which held "public" and
"members" and nothing else. A tier requirement is written "tier:<slug>", so
content.visibility and discussion_group.visibility widen to forty. upload
.visibility deliberately stays at ten: files are not tier-gated, and the
narrow column is part of what keeps them out.

Revision ID: f7c2a91d4e08
Revises: c41d7a9e3b52
Create Date: 2026-09-10

"""
import sqlalchemy as sa
from alembic import op

revision = 'f7c2a91d4e08'
down_revision = 'c41d7a9e3b52'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tier',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  autoincrement=True, nullable=False),
        sa.Column('org_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('slug', sa.String(length=30), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by_id',
                  sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=True),
        sa.Column('updated_by_id',
                  sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id'],
                                name=op.f('fk_tier_created_by_id_user'),
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['user.id'],
                                name=op.f('fk_tier_updated_by_id_user'),
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'],
                                name=op.f('fk_tier_org_id_organization'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tier')),
        sa.UniqueConstraint('org_id', 'slug', name='uq_tier_org_slug'),
    )
    op.create_index(op.f('ix_tier_org_id'), 'tier', ['org_id'], unique=False)

    connection = op.get_bind()
    # One Free tier per organization, so every membership has somewhere to
    # sit before the column that points at it becomes required. The
    # timestamps come from the database rather than from Python: both
    # engines have CURRENT_TIMESTAMP, and binding a datetime through raw
    # SQL goes through an adapter SQLite has deprecated.
    connection.execute(sa.text("""
        INSERT INTO tier (org_id, name, slug, rank, is_active,
                          created_by_id, updated_by_id,
                          created_at, updated_at)
        SELECT id, 'Free', 'free', 1, TRUE, NULL, NULL,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
          FROM organization
    """))

    op.add_column('membership',
                  sa.Column('tier_id',
                            sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                            nullable=True))
    connection.execute(sa.text("""
        UPDATE membership
           SET tier_id = (SELECT tier.id FROM tier
                           WHERE tier.org_id = membership.org_id
                           ORDER BY tier.rank, tier.id
                           LIMIT 1)
    """))
    # A membership whose organization is gone has no tier to point at and
    # would block the NOT NULL. There should be none, because the foreign
    # key cascades -- but SQLite only enforces foreign keys when asked to,
    # and an installation that ran without `PRAGMA foreign_keys=ON` is
    # exactly how such rows accumulate. Deleting them silently would throw
    # away memberships nobody knew existed and no downgrade could return,
    # so this stops and says how many there are instead.
    orphans = connection.scalar(
        sa.text('SELECT COUNT(*) FROM membership WHERE tier_id IS NULL'))
    if orphans:
        raise RuntimeError(
            f'{orphans} membership row(s) belong to no organization and so '
            'have no tier to point at. They predate this migration and it '
            'will not guess what to do with them. Inspect them with: '
            'SELECT * FROM membership WHERE org_id NOT IN '
            '(SELECT id FROM organization);')

    # SQLite rebuilds the table to add the constraint and the key.
    with op.batch_alter_table('membership') as batch:
        batch.alter_column('tier_id',
                           existing_type=sa.BigInteger().with_variant(
                               sa.Integer(), 'sqlite'),
                           nullable=False)
        batch.create_foreign_key(op.f('fk_membership_tier_id_tier'),
                                 'tier', ['tier_id'], ['id'],
                                 ondelete='RESTRICT')
        batch.create_index(op.f('ix_membership_tier_id'), ['tier_id'],
                           unique=False)

    # An invitation carries the tier its acceptor will land on. Nullable,
    # so an invitation written before this migration keeps working.
    with op.batch_alter_table('invitation') as batch:
        batch.add_column(sa.Column(
            'tier_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            nullable=True))
        batch.create_foreign_key(op.f('fk_invitation_tier_id_tier'),
                                 'tier', ['tier_id'], ['id'],
                                 ondelete='SET NULL')

    with op.batch_alter_table('content') as batch:
        batch.alter_column('visibility', existing_type=sa.String(length=10),
                           type_=sa.String(length=40), existing_nullable=False)
    with op.batch_alter_table('discussion_group') as batch:
        batch.alter_column('visibility', existing_type=sa.String(length=10),
                           type_=sa.String(length=40), existing_nullable=False)


def downgrade():
    with op.batch_alter_table('invitation') as batch:
        batch.drop_constraint(op.f('fk_invitation_tier_id_tier'),
                              type_='foreignkey')
        batch.drop_column('tier_id')
    with op.batch_alter_table('discussion_group') as batch:
        batch.alter_column('visibility', existing_type=sa.String(length=40),
                           type_=sa.String(length=10), existing_nullable=False)
    with op.batch_alter_table('content') as batch:
        batch.alter_column('visibility', existing_type=sa.String(length=40),
                           type_=sa.String(length=10), existing_nullable=False)
    with op.batch_alter_table('membership') as batch:
        batch.drop_index(op.f('ix_membership_tier_id'))
        batch.drop_constraint(op.f('fk_membership_tier_id_tier'),
                              type_='foreignkey')
        batch.drop_column('tier_id')
    op.drop_index(op.f('ix_tier_org_id'), table_name='tier')
    op.drop_table('tier')
