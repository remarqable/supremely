# Deployment

How to run Supremely on a server you control.

There are two supported paths. Both run the same container image and end up
with the same application; they differ in how much of the host setup you hand
over.

| | [Install script](install.md) | [Manual](manual.md) |
|---|---|---|
| Host | Ubuntu 22.04 or 24.04 LTS | Anything that runs Docker |
| Reverse proxy and TLS | Caddy, configured for you | You provide it |
| Updates, backups, rollback | Built in | You provide them |
| Good for | A dedicated server or VM for Supremely | A host that is not Ubuntu, or a server where you already run a proxy |

If you have a fresh Ubuntu server and no strong opinions, use the install
script. If you already run a reverse proxy, or you are deploying somewhere
that is not Ubuntu, read the manual guide. It also documents the pieces the
script sets up on your behalf, which is worth reading either way.

For trying Supremely on your own machine rather than deploying it, the
[quick start](../../README.md#quick-start) is faster than both.

Once it is running, [Email](email.md) covers connecting a provider, which is
optional and needed only for newsletters, notification email and
self-service password resets.

## What you are deploying

Two containers from one image:

- **web** serves the application with Gunicorn, and is the only container that
  runs database migrations.
- **worker** runs background jobs: sending newsletters, notifications, and
  anything else queued through the jobs system.

Both mount the same data directory, and they need to keep sharing it even if
you move to PostgreSQL. Uploads, installed themes, and the generated secret key
all live there, and two containers with different secret keys cannot read each
other's sessions.

With the default SQLite database they also share a single database file, so
they must run on the same host.

## What you need

- A host with Docker.
- A domain name, if you want HTTPS. Supremely runs over plain HTTP on a bare IP
  address, which is fine for a trial, but sessions travel unencrypted and
  organizations get no subdomains of their own.
- Nothing else. Email is optional everywhere in Supremely, including at
  install time, and PostgreSQL is optional until SQLite stops being enough.

## Multi-tenancy affects your proxy

Supremely is multi-tenant. One installation serves the main site, a subdomain
for each organization, and any custom domains those organizations verify.

That means your reverse proxy has to obtain certificates for hostnames that
did not exist when you configured it. Both guides explain how, because getting
it wrong produces an installation that works for the main domain and quietly
fails for everything else.
