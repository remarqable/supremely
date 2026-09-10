"""Discussion queries that answer without a page behind them."""

from flask import g

from app.models import DiscussionGroup, Post


def test_nothing_is_readable_off_tenant(app, acme, globex):
    """The installation paths (/setup, /admin, /auth) resolve no
    organization, and there the global tenant filter stands down. A query
    that ran anyway would gather up every community at once."""
    with app.test_request_context('/admin'):
        assert DiscussionGroup.readable_ids() == []
        assert Post.pinned_for_rail() == []


def test_a_caller_can_hand_over_the_listing_it_already_has(app, acme):
    """The discussions directory has loaded every group by the time it needs
    their ids, so it passes them in. An implementation that fetched its own
    would answer the same on a real page and cost a second query."""
    with app.test_request_context(base_url='http://acme.example.test'):
        g.org = acme
        assert DiscussionGroup.readable_ids([]) == []
        welcome = DiscussionGroup.query.filter_by(slug='welcome').one()
        assert DiscussionGroup.readable_ids([welcome]) == [welcome.id]
