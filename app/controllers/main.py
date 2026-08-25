"""Main controller: installation landing, organization home, health."""

from flask import Blueprint, g, redirect, render_template, url_for
from flask_login import current_user

bp = Blueprint('main', __name__)


@bp.route('/')
def index():
    if g.org is not None:
        return render_template('orgs/home.html', org=g.org)

    # Bare installation domain with zero or multiple organizations.
    if current_user.is_authenticated:
        return redirect(url_for('orgs.launcher'))
    return render_template('main/index.html')


@bp.route('/health')
def health():
    from app import APP_VERSION
    return {'status': 'ok', 'version': APP_VERSION}
