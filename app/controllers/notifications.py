"""In-app notification UI: unread count, listing, mark read."""

from flask import Blueprint, abort, g, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.notification import Notification
from app.platform.authz import org_required

bp = Blueprint('notifications', __name__, url_prefix='/notifications')


@bp.route('/')
@org_required
@login_required
def index():
    notifications = Notification.for_user(current_user.id)
    return render_template('members/notifications.html',
                           notifications=notifications)


@bp.route('/read-all', methods=['POST'])
@org_required
@login_required
def read_all():
    Notification.mark_all_read(current_user.id, g.org.id)
    return redirect(url_for('notifications.index'))


@bp.route('/<int:notification_id>/read', methods=['POST'])
@org_required
@login_required
def read_one(notification_id):
    notification = db.session.get(Notification, notification_id)
    if notification is None or notification.user_id != current_user.id:
        abort(404)
    notification.mark_read()
    url = (notification.payload or {}).get('url')
    return redirect(url or url_for('notifications.index'))
