"""The release version lives in three places and they have to agree.

APP_VERSION is what the application reports and what a Docker image is
tagged with; pyproject.toml is what the package says; CHANGELOG.md is what
a person reads. A bump that misses one of them ships an image whose tag
does not match what it says about itself.
"""

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from app import APP_VERSION, version_label

# These shell out. A machine without them is not a broken build.
needs_git = pytest.mark.skipif(shutil.which('git') is None,
                               reason='git is not installed')
needs_make = pytest.mark.skipif(shutil.which('make') is None,
                                reason='make is not installed')

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


def test_a_build_names_itself_and_a_source_checkout_does_not(monkeypatch):
    """An installation has to be able to say which build it is running.

    From source there is no build to name, so it says so rather than
    claiming one: an operator reading 0.1.0 on a working copy and 0.1.0 on
    a server would have no way to tell they are different code.
    """
    monkeypatch.delenv('APP_BUILD', raising=False)
    assert version_label() == f'{APP_VERSION}-dev'

    monkeypatch.setenv('APP_BUILD', '51')
    assert version_label() == f'{APP_VERSION}+build.51'

    # Whitespace from a build argument that was passed empty is not a build.
    monkeypatch.setenv('APP_BUILD', '  ')
    assert version_label() == f'{APP_VERSION}-dev'


@needs_make
def test_the_image_is_built_with_the_number_the_script_worked_out():
    """The Dockerfile takes the number and the Makefile supplies it. Both
    halves have to be there, or an image reports itself as running from
    source in production."""
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert 'ARG APP_BUILD' in dockerfile
    assert 'APP_BUILD=${APP_BUILD}' in dockerfile

    # BUILD on the command line, which beats everything a makefile can set:
    # otherwise this reads whatever the developer's git-ignored
    # Makefile.local happens to define, and asserts about their machine
    # rather than about the recipe.
    built = subprocess.run(['make', '-n', 'image', 'BUILD=probe'], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
    # Continuations joined first: make leaves a backslash where the missing
    # value would be, and a backslash is not whitespace, so a naive check
    # reads it as a value and an empty flag slips through.
    recipe = ' '.join(built.stdout.replace('\\\n', ' ').split())
    assert '--build-arg APP_BUILD=probe' in recipe
    # Every variable the recipe names has to have a value. An earlier edit
    # deleted PLATFORMS while rewriting the block above it, and the recipe
    # went out with an empty --platform, which docker reads as the next
    # argument and refuses. Nothing noticed, because the only test of this
    # recipe read the file rather than what make made of it.
    # Each flag's value checked for its shape rather than its next token: an
    # empty -t still leaves ":latest" behind, which reads as a value.
    assert re.search(r'--platform linux/\S+', recipe)
    assert re.search(r'--builder \S+ ', recipe)
    assert re.search(r'-t \S+/\S+:latest', recipe)
    assert '--push' in recipe


def _build_number(repo: Path) -> str:
    """What an image built from `repo` would be stamped with.

    The same script `make image` runs, so this tests the rule that ships
    rather than a copy of it kept in step by hand.
    """
    done = subprocess.run([str(ROOT / 'scripts' / 'build-number.sh')],
                          cwd=repo, capture_output=True, text=True, timeout=60)
    return done.stdout.strip()


@needs_git
def test_the_build_number_counts_from_the_release_and_never_guesses(tmp_path):
    """Four cases, and the first is why this test exists.

    With no tag, the tidier spelling of this counted zero rather than
    counting the history: the range collapses to "..HEAD", git reads that as
    HEAD..HEAD, counts nothing and exits successfully, so a fallback placed
    after a failure never runs. A shallow CI checkout has no tags, so every
    image built there would have called itself build 0.
    """
    # A machine whose git signs commits, runs hooks or seeds templates would
    # otherwise fail here for reasons that have nothing to do with the code.
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}

    def git(*args, at):
        subprocess.run(['git', *args], cwd=at, check=True, env=env,
                       capture_output=True, text=True, timeout=60)

    repo = tmp_path / 'repo'
    repo.mkdir()
    git('init', '-q', '.', at=repo)
    git('config', 'user.email', 'test@example.com', at=repo)
    git('config', 'user.name', 'Test', at=repo)
    for n in range(3):
        (repo / 'f').write_text(str(n))
        git('add', 'f', at=repo)
        git('commit', '-qm', f'commit {n}', at=repo)

    assert _build_number(repo) == '3'          # no tag: the whole history

    git('tag', 'v0.1.0', at=repo)
    assert _build_number(repo) == '0'          # nothing since the release

    (repo / 'f').write_text('again')
    git('add', 'f', at=repo)
    git('commit', '-qm', 'after the release', at=repo)
    assert _build_number(repo) == '1'

    # Only a release resets the count. A nightly, and a pre-release of the
    # next version, are both tags on a branch; counting from either would
    # say "one commit since the release" about a release months back.
    git('tag', 'nightly-2026-09-09', at=repo)
    assert _build_number(repo) == '1'
    git('tag', 'v1.0.0-rc1', at=repo)
    assert _build_number(repo) == '1'
    git('tag', 'v0.2.0', at=repo)
    assert _build_number(repo) == '0'      # a real release does reset it

    # Nowhere to count from: no number rather than a wrong one, and the
    # application then calls itself a source build.
    bare = tmp_path / 'not-a-repo'
    bare.mkdir()
    assert _build_number(bare) == ''


@needs_git
def test_a_shallow_checkout_reports_no_build_rather_than_a_wrong_one(tmp_path):
    """The case the script exists for, and the one that is easy to get
    plausibly wrong.

    A shallow clone holds only part of the history, so counting it counts
    the fetch depth: this repository answers 1 at --depth=1 where the truth
    is 51. A CI checkout is shallow by default, so that is the common case,
    and a believable wrong number is worse than none at all.
    """
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}

    def git(*args, at):
        subprocess.run(['git', *args], cwd=at, check=True, env=env,
                       capture_output=True, text=True, timeout=60)

    origin = tmp_path / 'origin'
    origin.mkdir()
    git('init', '-q', '.', at=origin)
    git('config', 'user.email', 'test@example.com', at=origin)
    git('config', 'user.name', 'Test', at=origin)
    for n in range(4):
        (origin / 'f').write_text(str(n))
        git('add', 'f', at=origin)
        git('commit', '-qm', f'commit {n}', at=origin)

    # file://, because git ignores --depth for a plain path and hands back a
    # full clone that would make this test pass without proving anything.
    shallow = tmp_path / 'shallow'
    subprocess.run(['git', 'clone', '-q', '--depth', '1', '--no-tags',
                    f'file://{origin}', str(shallow)],
                   check=True, env=env, capture_output=True, timeout=60)
    depth = subprocess.run(['git', 'rev-parse', '--is-shallow-repository'],
                           cwd=shallow, capture_output=True, text=True,
                           timeout=60)
    assert depth.stdout.strip() == 'true'      # the clone really is shallow

    assert _build_number(shallow) == ''
    assert _build_number(origin) == '4'        # the same history, in full


def test_the_health_endpoint_answers_which_build_it_is(app, monkeypatch):
    """The machine-readable half of the question. A deploy check and a
    support request both read this, and two installations a week apart
    otherwise report the same release."""
    monkeypatch.setenv('APP_BUILD', '51')
    body = app.test_client().get('/health').get_json()
    assert body['status'] == 'ok'
    assert body['version'] == f'{APP_VERSION}+build.51'


@needs_make
def test_an_image_that_cannot_name_itself_is_not_pushed():
    """A number this cannot work out means a shallow clone or no tags, and
    the recipe refuses rather than pushing a :latest that calls itself a
    source build.

    Asserted on the order as well as the presence: the refusal has to come
    before anything with a side effect, or a failed build has already
    created a builder.
    """
    built = subprocess.run(['make', '-n', 'image', 'BUILD='], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
    lines = [line for line in built.stdout.splitlines() if line.strip()]
    assert 'test -n' in lines[0], lines[:2]
    assert 'docker' not in lines[0]
