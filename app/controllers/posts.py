"""Public post routes: listing, permalinks, category and tag archives."""

from flask import Blueprint, abort, g, redirect, request, url_for
from flask_login import current_user

from app.models.post import Category, Post
from app.platform.authz import org_required
from app.platform.theming import render_site

bp = Blueprint('posts', __name__)

PER_PAGE = 10


def _visible(query):
    from app.platform.authz import is_org_member
    if is_org_member() or (current_user.is_authenticated
                           and current_user.is_platform_admin):
        return query
    return query.filter_by(visibility='public')


@bp.route('/posts')
@org_required
def index():
    page_number = request.args.get('page', 1, type=int)
    pagination = _visible(Post.published_query()).paginate(
        page=page_number, per_page=PER_PAGE, error_out=False)
    return render_site(['site/posts.html'], posts=pagination.items,
                       pagination=pagination, archive_title=None)


@bp.route('/posts/<slug>')
@org_required
def show(slug):
    post = Post.published_by_slug(slug)
    if post is None:
        abort(404)
    if not post.visible_to_current_visitor():
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        abort(404)
    post_type = post.post_type
    return render_site(
        [f'site/post-{post.slug}.html', f'site/{post_type.template}.html',
         'site/post.html'],
        post=post, post_type=post_type)


@bp.route('/posts/category/<slug>')
@org_required
def category(slug):
    cat = Category.get_by_slug(slug)
    if cat is None:
        abort(404)
    page_number = request.args.get('page', 1, type=int)
    pagination = _visible(
        Post.published_query().filter(Post.categories.contains(cat))
    ).paginate(page=page_number, per_page=PER_PAGE, error_out=False)
    return render_site(['site/posts.html'], posts=pagination.items,
                       pagination=pagination, archive_title=cat.name)


@bp.route('/posts/tag/<tag>')
@org_required
def tag(tag):
    page_number = request.args.get('page', 1, type=int)
    pagination = _visible(Post.with_tag(tag)).paginate(
        page=page_number, per_page=PER_PAGE, error_out=False)
    return render_site(['site/posts.html'], posts=pagination.items,
                       pagination=pagination, archive_title=f'#{tag}')
