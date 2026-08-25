"""Content rendering: Markdown to sanitized HTML.

Authors are org admins, but their output is served to members and the public:
sanitize on render so content can never carry script.
"""

import markdown as md
import nh3

_ALLOWED_TAGS = {
    'a', 'abbr', 'blockquote', 'br', 'code', 'del', 'div', 'em', 'figure',
    'figcaption', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'img', 'li',
    'ol', 'p', 'pre', 'span', 'strong', 'sub', 'sup', 'table', 'tbody',
    'td', 'th', 'thead', 'tr', 'ul',
}

_ALLOWED_ATTRIBUTES = {
    'a': {'href', 'title'},
    'img': {'src', 'alt', 'title', 'width', 'height', 'loading'},
    'td': {'align'}, 'th': {'align'},
    'div': {'class'}, 'span': {'class'}, 'code': {'class'}, 'pre': {'class'},
}


def render_markdown(text: str) -> str:
    if not text:
        return ''
    html = md.markdown(text, extensions=['extra', 'sane_lists'])
    return nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES,
                     link_rel='noopener noreferrer')
