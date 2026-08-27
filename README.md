# Supremely

**Supremely is an open-source, multi-tenant platform for publishing,
memberships, newsletters, and community — being built in public.**

One codebase serves self-hosted installations, third-party SaaS providers,
and the official hosted service. The tenant is the **Organization**: a
website and community with pages, posts (typed structured content),
discussions, a newsletter audience, themes, and plugins. Email is optional
infrastructure everywhere — installing, publishing, and onboarding members
never require it.

## Quick start

```bash
git clone https://github.com/remarqable/supremely
cd supremely
docker compose up
```

Open http://localhost:8000 and complete the setup wizard (environment,
SQLite or PostgreSQL, your Platform Admin, optional email, first
Organization).

For local development without Docker:

```bash
make install   # uv sync
make css       # build Tailwind (downloads the standalone binary once)
make run       # migrate + dev server on :8000
make test      # pytest
```

Useful commands: `flask users reset-password EMAIL` (recovery without
email), `flask jobs run` (background worker), `flask setup reset`
(re-enable the wizard), `flask seed getsupremely` (dogfood site).

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — how the system works: tenancy, the content graph, visibility vs presentation, theming, plugins.
- [Themes](docs/themes/README.md) — the theme designer's reference.
- [Building a theme](docs/themes/building-a-theme.md) — a start-to-finish tutorial (ships with the Trailhead example theme).

We are starting with the problem, the principles, and the roadmap — and letting the product emerge through shipping, learning, and iteration.

This repository is intentionally young. Expect change.

## Build in public

Supremely is being developed AI-first. AI may generate essentially all implementation code, but humans remain accountable for architecture, security, testing, review, product decisions, and what ultimately ships.

We do not treat AI-generated code as inherently good or bad. Code earns its place by meeting the project's standards.

The history of the project may include experiments, reversals, ugly intermediate states, and changed assumptions. That is part of building software.

## Project principles

- Simple beats clever.
- Working software beats speculative architecture.
- Humans own the decisions.
- AI accelerates implementation; it does not remove accountability.
- Security and correctness are requirements, not cleanup tasks.
- The repository should explain itself.
- Architecture decisions should be intentional and documented.
- We prefer small systems that can evolve over large systems designed for imagined futures.

See [MANIFESTO.md](MANIFESTO.md) and [docs/ENGINEERING.md](docs/ENGINEERING.md).

## Status

Supremely is pre-v0.1 and under active development.

See [docs/ROADMAP.md](docs/ROADMAP.md).

## Contributing

Supremely is open source and contributions are welcome. The project is still forming, so please read [CONTRIBUTING.md](CONTRIBUTING.md) before investing substantial effort.

## Security

Please do not disclose vulnerabilities publicly. See [SECURITY.md](SECURITY.md).

## License

See [LICENSE](LICENSE).

## Project

- Website: https://getsupremely.com
- Organization: https://supremely.org
