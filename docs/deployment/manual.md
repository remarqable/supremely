# Deploying manually

For hosts that are not Ubuntu, or servers where you already run a reverse
proxy. This is also what the [install script](install.md) does on your behalf,
so it is worth reading even if you use the script.

The steps below are in the order you need them. Prepare a data directory,
write a configuration file, start two containers, put a proxy in front, then
complete the setup wizard in your browser.

## The image

```
remarqable/supremely:latest
remarqable/supremely:0.1.0
```

Every release is published under its own version tag, and `latest` points at
the newest release, so `latest` is what you want unless you are pinning a
version deliberately. Version numbers and what changed in each are in
[CHANGELOG.md](../../CHANGELOG.md). Build your own with
`docker build -t supremely .` from a clone.

The image runs as a non-root user, **uid and gid 10001**, stores everything
under `/data`, and listens on port 8000.

The `docker-compose.yml` in the repository root **builds from source** rather
than pulling this image. It is there for local development and as a worked
example of the two services, not as a production deployment.

## 1. Prepare the data directory

Everything that survives a restart lives in `/data`: the SQLite database,
uploaded files, installed themes, the setup wizard's configuration, and a
generated secret key.

**The directory you mount must be owned by uid and gid 10001**, or the
application cannot write and the container will exit at startup:

```bash
sudo mkdir -p /srv/supremely/data
sudo chown -R 10001:10001 /srv/supremely/data
sudo chmod 750 /srv/supremely/data
```

A named Docker volume avoids this entirely, because the image sets ownership
on its own mount point. Use a bind mount only if you want the files somewhere
you choose.

## 2. Write a configuration file

Create `/srv/supremely/supremely.env`. This is the minimum for an HTTPS
installation on your own domain:

```bash
APP_ENV=production
BASE_DOMAIN=example.com
TRUSTED_PROXIES=1
```

Then lock it down, because it will hold secrets:

```bash
sudo chmod 600 /srv/supremely/supremely.env
```

That is genuinely all you need. SQLite on the data volume is the default, a
secret key is generated and persisted on first start, and email is optional
everywhere in Supremely.

For a plain HTTP installation with no domain, on a bare IP address for
example, add `SESSION_COOKIE_SECURE=false` as well. Without it the browser
refuses to store the session cookie over HTTP and nobody can sign in.

See [environment variables](#environment-variables) below for the full list.

## 3. Start both containers

Run the same image twice.

```bash
docker run -d --name supremely \
  --restart unless-stopped \
  --env-file /srv/supremely/supremely.env \
  -p 127.0.0.1:8000:8000 \
  -v /srv/supremely/data:/data \
  remarqable/supremely:latest

docker run -d --name supremely-worker \
  --restart unless-stopped \
  --env-file /srv/supremely/supremely.env \
  -v /srv/supremely/data:/data \
  remarqable/supremely:latest worker
```

The `worker` argument is the only difference. The web container serves the
application; the worker runs background jobs such as sending newsletters and
notifications.

**The web container owns migrations.** Its entrypoint runs `flask db upgrade`
before starting Gunicorn. The worker waits for the schema to appear rather
than running the same upgrade, so the two never migrate at once. If you scale
the web container beyond one replica, only one may run migrations; run them as
a separate deployment step instead.

Bind the web container to loopback, as above, whenever a proxy sits in front
of it. Binding to `0.0.0.0` publishes the application directly, with no TLS
and without the path blocking your proxy is expected to do.

Check it started:

```bash
curl -f http://127.0.0.1:8000/health && echo OK
```

## 4. Put a reverse proxy in front

Two endpoints must not be reachable from outside, and one behaviour is
specific to Supremely being multi-tenant.

### Block these paths

| Path | Why |
|---|---|
| `/tls-check` | Answers your proxy, over loopback, about which hostnames may receive a certificate. Public access lets anyone probe it. |
| `/_v/*` | The plugins' private mount, reached internally only. |

### Certificates for organization domains

One installation serves the main domain, a subdomain for each organization,
and any custom domains those organizations verify. Those hostnames appear
after your proxy is configured, so a fixed certificate list does not work.

Supremely answers `/tls-check` with whether a hostname belongs to this
installation, which lets a proxy issue certificates on demand without a
wildcard certificate or DNS provider credentials. In Caddy:

```caddyfile
{
    email you@example.com
    on_demand_tls {
        ask http://127.0.0.1:8000/tls-check
    }
}

(supremely_app) {
    handle /tls-check {
        respond 404
    }
    handle /_v/* {
        respond 404
    }
    handle {
        reverse_proxy 127.0.0.1:8000 {
            health_uri /health
            health_interval 2s
            health_timeout 5s
            fail_duration 10s
        }
    }
}

# The main domain. Its certificate is obtained at startup, so a bad DNS
# record fails loudly rather than at some later first request.
example.com {
    import supremely_app
}

# Organization subdomains and verified custom domains, issued on demand.
https:// {
    tls {
        on_demand
    }
    import supremely_app
}
```

With nginx, or any proxy that cannot issue certificates on demand, the main
domain plus a wildcard for organization subdomains will work. Custom domains
then need a certificate obtained per domain by whatever tooling you use.

Point DNS for organization subdomains and custom domains at this installation
with a CNAME or A record. HTTPS is the proxy's responsibility.

## 5. Complete the setup wizard

Open your domain in a browser. An uninitialized installation serves nothing
but the wizard, so there is no window where a half-configured site is public.

The wizard collects the environment, the database, your platform
administrator account, optional email settings, and your first organization.
Anything you set in `supremely.env` overrides what the wizard writes, so the
two do not fight.

If you ever need to run it again: `docker exec supremely flask setup reset`.

## Environment variables

Configuration layers in this order, each winning over the one before it:
built-in defaults, then `data/config.env` written by the setup wizard, then
real environment variables. So anything in your env file overrides what an
administrator chose in the interface.

| Variable | Default | What it does |
|---|---|---|
| `APP_ENV` | `dev` | Set to `production` for any real deployment. |
| `BASE_DOMAIN` | `localhost` | The installation's root domain. Organization subdomains resolve against it. |
| `SECRET_KEY` | generated | Signs sessions. If unset, a key is generated once and stored at `data/secret_key` so restarts and both containers agree. Set it explicitly if your data directory is not durable. |
| `DATABASE_URL` | SQLite in `/data` | A PostgreSQL URL to use PostgreSQL instead. `postgres://` and `postgresql://` are both accepted and normalised. |
| `TRUSTED_PROXIES` | `0` | The number of reverse proxies in front of the app. See below. |
| `SESSION_COOKIE_SECURE` | on outside dev | Set `false` only for a plain HTTP installation, otherwise nobody can sign in. |
| `WEB_CONCURRENCY` | `4` | Gunicorn worker processes. |
| `PORT` | `8000` | Port inside the container. |

### Set TRUSTED_PROXIES to your real hop count

It defaults to `0`, meaning no `X-Forwarded-*` header is believed, which is
correct when nothing sits in front of the application. Behind a single proxy,
set it to `1`.

Leaving it at `0` behind a proxy makes every request appear to come from the
proxy's own address. Rate limiting then counts all visitors against one
bucket, so a single caller can lock everybody out of signing in, and the
application cannot tell whether a request arrived over HTTPS.

### Do not quote values

Docker's `--env-file` is not a shell. Quotation marks become part of the
value, so this fails to parse at startup:

```
DATABASE_URL="postgresql://user:pass@host/db"    # wrong
DATABASE_URL=postgresql://user:pass@host/db      # right
```

## Upgrading

Pulling a new image is not enough on its own. A running container keeps the
image it started with, so it has to be replaced:

```bash
# Back up first, see below.
docker pull remarqable/supremely:latest

# Web container first: it applies the migrations.
docker rm -f supremely
docker run -d --name supremely \
  --restart unless-stopped \
  --env-file /srv/supremely/supremely.env \
  -p 127.0.0.1:8000:8000 \
  -v /srv/supremely/data:/data \
  remarqable/supremely:latest

curl -f http://127.0.0.1:8000/health && echo OK

# Then the worker, once the schema is up to date.
docker rm -f supremely-worker
docker run -d --name supremely-worker \
  --restart unless-stopped \
  --env-file /srv/supremely/supremely.env \
  -v /srv/supremely/data:/data \
  remarqable/supremely:latest worker
```

Removing a container destroys nothing: your data is in the mounted directory,
not in the container.

Migrations run forward automatically and are not reversed for you, so a
rollback means restoring a backup, not just starting the old image again.

## Backups

Back up the whole data directory. With SQLite, do not copy `app.db` while the
application is writing to it: the write-ahead log means you can capture a torn
database that refuses to restore. Take a consistent snapshot instead:

```bash
sqlite3 /srv/supremely/data/app.db ".backup '/tmp/app.db.backup'"
```

Then archive that snapshot together with the rest of the directory, excluding
`app.db-wal` and `app.db-shm`.

With PostgreSQL, back up the database with `pg_dump` or your provider's
tooling, and the data directory separately for uploads and configuration.

Backups contain your secret key, and your database password if it is in the
environment file. Store them accordingly.

## Troubleshooting

**The container starts and immediately exits.** Almost always the data
directory's ownership. Run `docker logs supremely`. Fix with
`sudo chown -R 10001:10001 /srv/supremely/data`.

**Nobody can sign in, and the login form just reloads.** The session cookie is
being rejected. Over plain HTTP, set `SESSION_COOKIE_SECURE=false`. Over
HTTPS, check that `BASE_DOMAIN` matches the domain you are actually visiting.

**Everyone is rate limited at once, or a single visitor locks out the
installation.** `TRUSTED_PROXIES` does not match the number of proxies in
front of the app. With one proxy it should be `1`.

**The main domain works but organization subdomains show a certificate
error.** Your proxy is not issuing certificates on demand. See the Caddy
configuration above, and check that DNS for the subdomain points at this
installation.

**The container will not start after editing the env file.** Look for
quotation marks around a value. Docker keeps them as part of the value.

**Background jobs never run.** Check the worker is up with `docker ps`, and
read `docker logs supremely-worker`. If it is looping on "waiting for database
migrations", the web container has not successfully migrated yet.
