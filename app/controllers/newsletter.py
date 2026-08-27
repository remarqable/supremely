"""Public newsletter routes: subscribe, confirm, unsubscribe.

Everything works without email: with no SMTP configured, subscriptions are
immediate (no double opt-in to deliver); publishing remains fully operable.
"""

from flask import Blueprint, abort, flash, g, redirect, request

from app.middleware.ratelimit import rate_limit
from app.models.newsletter import Subscriber
from app.platform.authz import org_required
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.mailer import is_email_configured
from app.platform.theming import render_site

bp = Blueprint('newsletter', __name__)
log = get_logger()


@bp.route('/newsletters')
@org_required
def archive():
    """Past issues, for members: sent deliveries whose post is still
    published. Visitors get the gate (tease-don't-hide), not a 404."""
    from flask import render_template
    from flask_login import current_user

    from app.models import Content
    from app.models.newsletter import Delivery
    from app.platform.i18n import t
    from app.platform.theming import render_gate
    if g.membership is None and not (current_user.is_authenticated
                                     and current_user.is_platform_admin):
        return render_gate(t('newsletters.archive_title'))
    issues = (Delivery.query.filter_by(status='done')
              .join(Delivery.content)
              .filter(Content.status == 'published')
              .order_by(Delivery.finished_at.desc()).limit(50).all())
    return render_template('community/newsletters.html', issues=issues)


@bp.route('/subscribe', methods=['GET', 'POST'])
@org_required
def subscribe():
    if request.method == 'POST':
        return _do_subscribe()
    return render_site(['subscribe.html'], done=False)


@rate_limit(limit=10, window=300)
def _do_subscribe():
    email = request.form.get('email', '')
    require_confirmation = is_email_configured()
    try:
        subscriber = Subscriber.subscribe(email, g.org.id,
                                          require_confirmation)
    except ValidationError as e:
        flash(e.message, 'error')
        return render_site(['subscribe.html'], done=False), 400

    if subscriber.status == 'pending':
        from app.platform.jobs import enqueue
        enqueue('newsletter.confirmation_email', org_id=g.org.id,
                subscriber_id=subscriber.id)
    log.info('subscriber_added', org_id=g.org.id, status=subscriber.status)
    return render_site(['subscribe.html'], done=True,
                       pending=subscriber.status == 'pending')


@bp.route('/subscribe/confirm/<token>', methods=['GET', 'POST'])
@org_required
def confirm(token):
    subscriber = Subscriber.by_token(token)
    if subscriber is None:
        abort(404)
    # Mutate only on POST so email/link prefetchers can't confirm on GET.
    if request.method == 'POST':
        subscriber.confirm()
        flash(t('newsletter.confirmed'), 'success')
        return redirect('/')
    return render_site(['confirm.html'], token=token)


@bp.route('/unsubscribe/<token>', methods=['GET', 'POST'])
@org_required
def unsubscribe(token):
    subscriber = Subscriber.by_token(token)
    if subscriber is None:
        abort(404)
    # Mutate only on POST so scanners/prefetchers can't unsubscribe on GET.
    if request.method == 'POST':
        subscriber.unsubscribe()
        return render_site(['unsubscribed.html'])
    return render_site(['unsubscribe.html'], token=token, email=subscriber.email)
