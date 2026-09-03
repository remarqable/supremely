"""The release version lives in three places and they have to agree.

APP_VERSION is what the application reports and what a Docker image is
tagged with; pyproject.toml is what the package says; CHANGELOG.md is what
a person reads. A bump that misses one of them ships an image whose tag
does not match what it says about itself.
"""

import re
import tomllib
from pathlib import Path

from app import APP_VERSION

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r'\d+\.\d+\.\d+')


def test_the_version_is_a_release_number():
    assert SEMVER.fullmatch(APP_VERSION)


def test_pyproject_agrees_with_the_application():
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert pyproject['project']['version'] == APP_VERSION


def test_the_changelog_leads_with_this_version():
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    first = re.search(r'^## \[(.+?)\] - (\d{4}-\d{2}-\d{2})$',
                      changelog, re.M)
    assert first, 'CHANGELOG.md has no released version heading'
    assert first.group(1) == APP_VERSION
