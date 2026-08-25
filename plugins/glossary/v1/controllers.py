from flask import Blueprint, flash, redirect, request

from app.platform.authz import org_required, require
from app.platform.errors import ValidationError
from app.platform.plugins import plugin_settings
from app.platform.theming import render_site

from .models import GlossaryTerm

bp = Blueprint('glossary_v1', __name__, template_folder='views')


@bp.route('/')
@org_required
def index():
    q = request.args.get('q', '').strip()
    query = GlossaryTerm.query          # tenant filter applies automatically
    if q:
        query = query.filter(GlossaryTerm.term.ilike(f'%{q}%'))
    terms = query.order_by(GlossaryTerm.term).all()
    return render_site(['glossary/index.html'], terms=terms, q=q,
                       glossary_settings=plugin_settings('glossary'))


@bp.route('/', methods=['POST'])
@org_required
@require('content.write')
def add_term():
    term = GlossaryTerm(term=request.form.get('term', ''),
                        definition=request.form.get('definition', ''))
    term.stamp_audit()
    try:
        term.save()
        flash('Term added.', 'success')
    except ValidationError as e:
        from app.extensions import db
        db.session.rollback()
        flash(e.message, 'error')
    from app.platform.plugins import plugin_url_for
    return redirect(plugin_url_for('index'))
