"""Internationalization with runtime-loaded JSON catalogs."""

import contextlib
import json
from pathlib import Path
from threading import RLock
from typing import Any

from flask import Flask, g, has_request_context, request, session

_translations: dict[str, dict[str, str]] = {}
_lock = RLock()

RTL_LANGUAGES = {'ar', 'fa', 'he', 'ur'}
DEFAULT_LANGUAGE = 'en'

AVAILABLE_LANGUAGES = {
    'en': 'English',
}


def init_i18n(app: Flask) -> None:
    lang_dir = Path(app.root_path) / 'lang'
    if lang_dir.exists():
        for lang_file in lang_dir.glob('*.json'):
            _load_language(lang_file.stem, lang_file)

    @app.before_request
    def detect_language():
        g.lang = _detect_language()
        g.is_rtl = g.lang in RTL_LANGUAGES

    @app.context_processor
    def inject_i18n():
        return {
            't': t,
            '_': t,
            'lang': get_lang(),
            'is_rtl': is_rtl(),
            'AVAILABLE_LANGUAGES': AVAILABLE_LANGUAGES,
        }


def _load_language(lang_code: str, file_path: Path) -> None:
    try:
        with open(file_path, encoding='utf-8') as f:
            data = json.load(f)
        with _lock:
            _translations[lang_code] = data
    except (OSError, json.JSONDecodeError) as e:
        print(f'Error loading {lang_code}: {e}')


def merge_translations(lang_code: str, entries: dict[str, str]) -> None:
    """Merge extra entries (plugin catalogs) into a language."""
    with _lock:
        _translations.setdefault(lang_code, {}).update(entries)


def _detect_language() -> str:
    lang = request.args.get('lang')
    if lang and lang in _translations:
        session['lang'] = lang
        return lang

    lang = session.get('lang')
    if lang and lang in _translations:
        return lang

    for lang, _q in request.accept_languages:
        code = lang.split('-')[0].lower()
        if code in _translations:
            return code

    return DEFAULT_LANGUAGE


def get_lang() -> str:
    if has_request_context():
        return getattr(g, 'lang', DEFAULT_LANGUAGE)
    return DEFAULT_LANGUAGE


def is_rtl() -> bool:
    return get_lang() in RTL_LANGUAGES


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate a key with {name} substitution. Falls back en -> key."""
    if lang is None:
        lang = get_lang()

    text = _get_translation(lang, key)
    if text is None and lang != 'en':
        text = _get_translation('en', key)
    if text is None:
        return key

    if kwargs:
        with contextlib.suppress(KeyError, IndexError):
            text = text.format(**kwargs)
    return text


def _get_translation(lang: str, key: str) -> str | None:
    with _lock:
        return _translations.get(lang, {}).get(key)
