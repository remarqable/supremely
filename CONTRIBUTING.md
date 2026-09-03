# Contributing to Supremely

Thanks for your interest in Supremely.

The project is young and may change quickly. Before undertaking a large contribution, open an issue so we can align on direction first. Issues are also the place for questions: there is no separate chat or forum.

## Before contributing

Please:

1. Read the [Manifesto](MANIFESTO.md).
2. Read the [Engineering Principles](docs/ENGINEERING.md).
3. Review relevant architecture decisions in `docs/adr/`.
4. Keep changes focused and understandable.

## Development philosophy

The maintainers use an AI-first development workflow. Contributors may use AI tools or not — the standard is the same.

You are responsible for what you submit.

Generated code should be understood, tested, reviewed, and consistent with project conventions.

## Running it locally

**Clone with submodules.** `blueprint/` is a git submodule, and a plain `git clone` leaves it empty:

```bash
git clone --recurse-submodules https://github.com/remarqable/supremely
```

If you already cloned without it: `git submodule update --init`.

You need [uv](https://docs.astral.sh/uv/) and `make`. uv installs Python itself, so you do not need to have 3.12 already.

```bash
make install   # uv sync
make css       # build Tailwind (downloads the standalone binary once)
make run       # dev server on :8000
make test      # pytest
```

The [README quick start](README.md#quick-start) covers the same ground plus the Docker path. `make` on its own lists every target.

Two things about the toolchain that trip people up:

- **Everything goes through `uv run`.** There is no `requirements.txt`, no venv to activate, and no `pip install`. Every `make` target already wraps it.
- **`make run` builds the dev schema from the models** (`flask dev sync-db`), it does not run migrations. Alembic migrations are the production and CI path (`make migrate`). Adding a table or a column just works — `sync-db` creates missing tables and adds missing columns (with the model's default for existing rows). Only an *incompatible* model change (a rename, a type change, a drop) needs `make reset` for a clean database.

`make demo` wipes and seeds a Demo Community you can click around (`admin@demo.test` / `password`). `make worker` runs the background job worker, which you need only if you are working on jobs, newsletters or notifications.

## Conventions of record

Two files are authoritative, and reading the relevant part of them first will save you a review round trip:

- **`blueprint/`** is the engineering pattern library, with its own `CLAUDE.md` and `patterns/`. Before adding a feature, find the pattern doc that covers it and follow it, rather than inventing a second way to do something the blueprint already answers.
- **`CLAUDE.md`** in the repository root records the decisions specific to Supremely: the tenancy model, the presentation seam, what belongs on the community surface versus in the console.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) is the orientation document if you are new to the codebase. [docs/themes/README.md](docs/themes/README.md) is the reference if you are building a theme.

A few rules are worth knowing before you write code, because they are easy to miss and CI or review will catch them:

- Business models inherit `OrgScoped`, and queries are **never** filtered by `org_id` by hand. A global tenant filter does it.
- `utcnow()` from `app/models/base.py`, never `datetime.utcnow()`.
- Logical CSS properties (`ms-`, `me-`, `ps-`, `pe-`, `text-start`), never `ml-`, `mr-` or `text-left`. Physical properties silently break right-to-left languages.
- User-visible strings go through `t()` / `_()` with a key in `app/lang/en.json`.

## Mobile

Supremely is responsive first. A phone gets the same template with Tailwind's
`sm:` / `md:` / `lg:` prefixes doing the rearranging, and that is what almost
every surface should do — one file to change, one thing to review, no chance
of the two versions drifting apart.

Where a phone needs a genuinely different layout rather than a narrower one,
add a mobile sibling of the template under `mobile/`:

```
app/views/manage/media.html         ->  app/views/manage/mobile/media.html
app/views/community/single.html     ->  app/views/community/mobile/single.html
app/views/themes/origin/single.html ->  app/views/themes/origin/mobile/single.html
```

Nothing else changes. Every render already goes through the device-aware
resolver (`app/platform/devices.py`), so adding a mobile screen is adding a
file, not editing Python, and a template with no mobile sibling — which is
nearly all of them — renders on a phone exactly as it does on a laptop.

Two rules worth keeping:

- **Reach for a `sm:` prefix first.** A dedicated mobile template is for a
  different information layout, not for a smaller one. Every one you add is a
  second file that has to be kept in step forever.
- **Test both.** `?device=mobile` on any URL pins the mobile layout for your
  session so you can click around on a laptop; `?device=auto` releases it.
  In tests, send a mobile `User-Agent` header.

## Releases and versioning

Supremely is versioned with [semantic versioning](https://semver.org), and
the version lives in three places that have to agree: `APP_VERSION` in
`app/__init__.py`, `version` in `pyproject.toml`, and the top entry of
[CHANGELOG.md](CHANGELOG.md). A test fails if they drift apart, so bump all
three in the same commit.

The changelog follows [Common Changelog](https://common-changelog.org):
one section per release, newest first, grouped into Added / Changed /
Fixed / Removed, written for someone deciding whether to upgrade rather
than for someone reading the diff.

Publishing a release is a maintainer action. Each one is pushed as a Docker
image under its own version tag and under `latest`, so an installation can
pin a version, and `latest` is the newest release.

## Reporting a security issue

Do not open a public issue for a suspected vulnerability. [SECURITY.md](SECURITY.md) has the private reporting route, which is GitHub's private vulnerability reporting or an email address if you would rather not use GitHub.

## Changes

Good contributions generally:

- solve a concrete problem;
- avoid unnecessary dependencies and abstraction;
- include appropriate tests;
- update documentation when behavior changes;
- preserve security and backwards compatibility where applicable.

Large architectural changes should begin with discussion and may require an ADR.

## Before you open a pull request

Run these three. They are the same checks CI runs, so a green local run is usually a green build:

```bash
make test                     # the full suite
make lint                     # ruff
make css                      # only if you touched a template
```

**If you touched any template, run `make css` and commit the rebuilt `app/static/css/app.css`.** This is the single most common reason a first pull request goes red: CI rebuilds the stylesheet and fails if the committed one is stale. An uncompiled Tailwind class renders unstyled in production and nowhere else, which is why the check exists.

The suite runs on in-memory SQLite by default. CI also runs it against PostgreSQL, and you can do the same locally if your change touches queries, column types or migrations:

```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/test make test
```

## Commits

Prefer clear, focused commits with useful messages.

This is an open-source project, so the git history is public documentation. Write for a reader with no project context: a plain imperative subject, a sentence orienting them, then what changed and why in everyday language. Name concrete files as anchors.

Project history does not need to pretend development was perfect, but commits should help future maintainers understand what changed and why.

## After you open a pull request

Three CI jobs run on every pull request:

- **test**, twice: once on SQLite and once against PostgreSQL 16. It also checks that the built stylesheet is current.
- **lint**: ruff.
- **audit**: `pip-audit` against the locked dependencies. It reports findings on the run summary and does not fail the build, so a fresh advisory in a dependency will not block your work.

Review looks for the things in [Engineering Principles](docs/ENGINEERING.md): whether the change is understandable, whether it follows the blueprint pattern for its area rather than introducing a second one, whether the tests cover the behavior that matters, and whether anything touching permissions, tenancy or user input has been thought through.

If something in this file turns out to be wrong or missing, that is worth a pull request too.

## Licensing of contributions

Supremely is licensed under [AGPL-3.0](LICENSE). By opening a pull request you agree that your contribution is offered under that same licence, and that you have the right to offer it. There is no separate contributor licence agreement to sign.

## Conduct

Participation in this project is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
