"""Provisioning seed content for the community forum, with vertical overlays.

Every new community lands with a small, non-empty forum that demonstrates
the model without feeling padded: three groups, six posts, all owned by the
community owner and fully editable or deletable — no special-casing.

Vertical plugins can register an OVERLAY that replaces one group and/or
overrides default posts by "Group/Title" path. Overlays are data, not code:
they are validated against the rules below and the resolver falls back
silently to the default structure on any validation failure. No overlay is
registered in v1 — the hook ships now because retrofitting seed content into
an already-shipped provisioner is the expensive version.
"""

import copy

from app.platform.logger import get_logger

log = get_logger()

# --- The default structure (v1) ----------------------------------------------

MAX_GROUPS = 3
MAX_POSTS_PER_GROUP = 2

DEFAULT_SEED = {
    'groups': [
        {
            # The default landing group: first in every listing and the New
            # Post default; public so anonymous visitors land somewhere alive.
            'name': 'Welcome', 'slug': 'welcome', 'visibility': 'public',
            'position': 1,
            'posts': [
                {
                    'title': 'Welcome to the community',
                    'pinned': True, 'seeded': True,
                    'body': (
                        'Welcome! This is the place for members to connect, '
                        'share ideas, ask questions, and learn from each '
                        'other.\n\n'
                        "Take a look around and introduce yourself when "
                        "you're ready."),
                },
                {
                    'title': 'Introduce yourself',
                    'pinned': True,
                    'body': (
                        'Say hello and tell everyone a little about '
                        'yourself.\n\n'
                        'What brought you here? What are you interested in? '
                        'What are you hoping to get from the community?'),
                },
            ],
        },
        {
            'name': 'General', 'slug': 'general', 'visibility': 'members',
            'position': 2,
            'posts': [
                {
                    'title': "What's everyone working on?",
                    'body': (
                        'What are you working on, thinking about, reading, '
                        'building, or exploring right now?\n\n'
                        'Share whatever is interesting to you.'),
                },
                {
                    'title': 'Open discussion',
                    'body': (
                        "A place for conversations that don't need their own "
                        'topic yet.\n\n'
                        'Questions, observations, links, recommendations, '
                        'anything relevant to the community is welcome.'),
                },
            ],
        },
        {
            'name': 'Ideas & Feedback', 'slug': 'ideas-feedback',
            'visibility': 'members', 'position': 3,
            'posts': [
                {
                    'title': 'Share an idea',
                    'body': (
                        'Have an idea for something the community should '
                        'discuss, organize, create, or try?\n\n'
                        'Post it here and see what others think.'),
                },
                {
                    'title': 'Help us improve this community',
                    'body': (
                        'What would make this community more useful to '
                        'you?\n\n'
                        'Share suggestions about topics, groups, events, '
                        'features, or anything else you would like to see.'),
                },
            ],
        },
    ],
}

# --- Overlay registry ---------------------------------------------------------

SEED_OVERLAYS: dict[str, dict] = {}


def register_seed_overlay(vertical: str, overlay: dict) -> None:
    """Register a vertical's seed overlay. Raises ValueError on an invalid
    shape — registration is the loud moment; resolution stays silent."""
    validate_overlay(overlay)
    SEED_OVERLAYS[vertical] = overlay


def validate_overlay(overlay: dict) -> None:
    if not isinstance(overlay, dict):
        raise ValueError('Overlay must be a dict')
    replaces = overlay.get('replaces_group')
    group = overlay.get('group')
    if (replaces is None) != (group is None):
        raise ValueError('replaces_group and group come together')
    if replaces is not None:
        if not any(g['name'] == replaces for g in DEFAULT_SEED['groups']):
            raise ValueError(f'Unknown group to replace: {replaces!r}')
        if not isinstance(group, dict) or not group.get('name'):
            raise ValueError('Replacement group needs a name')
        posts = overlay.get('posts', [])
        if not isinstance(posts, list) or len(posts) > MAX_POSTS_PER_GROUP:
            raise ValueError(
                f'Replacement group is capped at {MAX_POSTS_PER_GROUP} posts')
        for post in posts:
            if not (isinstance(post, dict)
                    and post.get('title') and post.get('body')):
                raise ValueError('Overlay posts need a title and a body')
    default_paths = {f"{g['name']}/{p['title']}"
                     for g in DEFAULT_SEED['groups'] for p in g['posts']}
    for override in overlay.get('overrides', []):
        if not isinstance(override, dict):
            raise ValueError('Overrides must be dicts')
        if override.get('target') not in default_paths:
            raise ValueError(f"Unknown override target: "
                             f"{override.get('target')!r}")
        if not (override.get('title') or override.get('body')):
            raise ValueError('An override must change a title or a body')


def resolve_seed(vertical: str | None = None) -> dict:
    """The seed structure for a vertical: the default, with that vertical's
    overlay applied. Any overlay problem falls back silently to the default —
    provisioning must never fail over seed copy."""
    seed = copy.deepcopy(DEFAULT_SEED)
    overlay = SEED_OVERLAYS.get(vertical) if vertical else None
    if overlay is None:
        return seed
    try:
        validate_overlay(overlay)
        return _apply_overlay(seed, overlay)
    except (ValueError, KeyError, TypeError) as exc:
        log.warning('seed_overlay_invalid', vertical=vertical, error=str(exc))
        return copy.deepcopy(DEFAULT_SEED)


def _apply_overlay(seed: dict, overlay: dict) -> dict:
    if overlay.get('replaces_group'):
        for index, group in enumerate(seed['groups']):
            if group['name'] == overlay['replaces_group']:
                replacement = overlay['group']
                group.update(
                    name=replacement['name'],
                    slug=replacement.get(
                        'slug', _slugify(replacement['name'])),
                    position=replacement.get('position', group['position']),
                )
                if overlay.get('posts'):
                    group['posts'] = [
                        {'title': p['title'], 'body': p['body'],
                         'pinned': bool(p.get('pinned'))}
                        for p in overlay['posts'][:MAX_POSTS_PER_GROUP]]
                seed['groups'][index] = group
                break
    for override in overlay.get('overrides', []):
        group_name, _, title = override['target'].partition('/')
        for group in seed['groups']:
            if group['name'] != group_name:
                continue
            for post in group['posts']:
                if post['title'] == title:
                    post['title'] = override.get('title', post['title'])
                    post['body'] = override.get('body', post['body'])
    return seed


def _slugify(name: str) -> str:
    import re
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return slug or 'group'
