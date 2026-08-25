"""Main controller: installation landing, organization home, health."""

from flask import (Blueprint, abort, current_app, g, redirect,
                   render_template, request, url_for)
from flask.typing import ResponseReturnValue
from flask_login import current_user

from app.middleware.ratelimit import rate_limit
from app.platform.tenant import org_for_host

bp = Blueprint('main', __name__)


@bp.route('/')
def index():
    if g.org is not None:
        from .site import render_org_home
        return render_org_home()

    # Bare installation domain with zero or multiple organizations.
    if current_user.is_authenticated:
        return redirect(url_for('orgs.launcher'))
    return render_template('main/index.html')


@bp.route('/health')
def health():
    from app import APP_VERSION
    return {'status': 'ok', 'version': APP_VERSION}


@bp.route('/tls-check')
@rate_limit(limit=300, window=60)
def tls_check() -> ResponseReturnValue:
    """Caddy's on-demand TLS gate (`ask`): may this host get a certificate?

    Any 2xx permits issuance, anything else denies it. Without the gate a
    stranger pointing their domain at this server could make us request
    certificates for it until the ACME rate limit is hit.

    The host-to-organization decision belongs to the platform layer, not
    here: see app/platform/tenant.org_for_host.
    """
    host = (request.args.get('domain') or '').strip().lower().split(':')[0]
    if not host:
        abort(403)

    # The installation's own domain, answered before touching the database so
    # a fresh install can obtain its certificate before the wizard has run.
    if host == current_app.config['BASE_DOMAIN'].split(':')[0].lower():
        return '', 204

    if org_for_host(host) is not None:
        return '', 204
    abort(403)
