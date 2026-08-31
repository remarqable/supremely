# Security Policy

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a suspected security vulnerability.

Report it through [GitHub's private vulnerability reporting](https://github.com/remarqable/supremely/security/advisories/new). Only you and the maintainers can see the report, and we can publish an advisory from it once a fix has shipped.

If you would rather not use GitHub, or do not have an account, email dev@remarqable.io instead.

Include enough information to reproduce and evaluate the issue, but do not include unnecessary sensitive data.

## Supported versions

Supremely is currently pre-v1.0.0. Until stable releases exist, security fixes will generally target the current development version.

## Dependency alerts

Dependabot alerts are enabled, so a published advisory against anything in `uv.lock` is raised automatically, and Dependabot opens a pull request when it can fix one. Routine version bumps arrive as one grouped pull request a week.

CI also runs `pip-audit` against the locked versions on every push and pull request. It reports findings on the run summary and deliberately does not fail the build: an advisory published overnight should not block unrelated work, and a red build nobody caused is a build people stop reading.

An alert is triaged by whether the vulnerable code path is actually reachable from Supremely, not by severity score alone. Fixes are applied through `uv` so that `uv.lock` and `pyproject.toml` stay in step.

## AI-generated code

Supremely uses AI extensively during development. AI-generated code receives no exemption from normal security expectations. Generated dependencies, authentication logic, data handling, permissions, cryptography, and external integrations require appropriate human review and testing before release.
