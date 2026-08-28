"""A failed sign in costs the same whether or not the account exists.

The wording, the status and the body length were already identical for both
cases. The clock was not: checking for a missing user first and stopping
there meant an unknown address returned after one indexed lookup while a
real one paid a full password comparison, which is slow by design. The gap
was over a hundredfold, so the careful wording protected nothing.
"""

import statistics
import time

import pytest

from app.models import User
from app.models.user import verify_credentials

PASSWORD = 'correct-horse-9'


@pytest.fixture
def account(app):
    return User.create(email='real@example.test', password=PASSWORD,
                       name='Real')


def test_a_missing_account_still_pays_for_a_comparison(app, monkeypatch):
    """The deterministic half. Timing is measured below, but asserting on
    the clock alone would be flaky, so this pins the mechanism."""
    calls = []
    import app.models.user as user_module
    real = user_module.check_password_hash
    monkeypatch.setattr(user_module, 'check_password_hash',
                        lambda *a, **kw: calls.append(a) or real(*a, **kw))

    assert verify_credentials(None, 'anything') is False
    assert len(calls) == 1, 'no comparison ran for a missing account'


def test_the_right_password_still_works(app, account):
    assert verify_credentials(account, PASSWORD) is True


def test_the_wrong_password_still_fails(app, account):
    assert verify_credentials(account, 'not-the-password') is False


def test_a_suspended_account_is_refused_after_the_comparison(app, account):
    """Judged after the hash, not before, so being suspended is not itself
    something the clock can reveal."""
    account.is_active = False
    assert verify_credentials(account, PASSWORD) is False


def test_the_two_cases_take_comparable_time(app, account):
    """Generous on purpose. The gap was over a hundredfold; anything in the
    same order of magnitude has closed it, and a tight bound would flake on
    shared hardware."""
    def median_of(user):
        return statistics.median(
            [_timed(user) for _ in range(7)])

    def _timed(user):
        start = time.perf_counter()
        verify_credentials(user, 'wrong-password-entirely')
        return time.perf_counter() - start

    verify_credentials(account, 'warm the cache')
    present, absent = median_of(account), median_of(None)
    high, low = max(present, absent), min(present, absent)
    assert high / low < 5, f'{present=:.4f} {absent=:.4f}'
