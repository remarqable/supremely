# Installing with the install script

A single command that turns a fresh Ubuntu server into a running Supremely
installation: Docker, Caddy with automatic HTTPS, both containers, a health
watchdog, and backups.

## Requirements

- Ubuntu 22.04 or 24.04 LTS. The script checks and refuses to run on anything
  else.
- Root, through `sudo`.
- A domain pointed at the server, if you want HTTPS. You can install without
  one and add it later.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/remarqable/supremely/main/scripts/installer | sudo bash
```

The script asks for a domain and an email address for Let's Encrypt, then
installs everything and prints the URL to open.

### Reading it first

Piping a script into `sudo bash` asks you to trust a URL. If you would rather
look before running it, which is reasonable:

```bash
curl -fsSL https://raw.githubusercontent.com/remarqable/supremely/main/scripts/installer -o installer
less installer
sudo bash installer
```

The script is `scripts/installer` in this repository, so you can also read it
on GitHub, or clone the repository and run your own copy.

### Without prompts

```bash
SUPREMELY_DOMAIN=example.com SUPREMELY_EMAIL=you@example.com \
  bash -c "curl -fsSL https://raw.githubusercontent.com/remarqable/supremely/main/scripts/installer | sudo -E bash"
```

Use `SUPREMELY_DOMAIN=ip` to install on the server's public IP over plain
HTTP, with no certificate.

## After installing

Open the URL and complete the setup wizard: environment, database, your
platform administrator account, optional email, and your first organization.

Nothing is exposed until you do. An installation that has not been set up
serves nothing but the wizard.

## Running it afterwards

The script installs itself to `/opt/supremely/installer` and is also the
update and maintenance tool.

```bash
sudo /opt/supremely/installer --update      # install or update; safe to repeat
sudo /opt/supremely/installer --configure   # change domain or HTTPS settings
sudo /opt/supremely/installer --check       # is a newer image available
sudo /opt/supremely/installer --info        # version and status
sudo /opt/supremely/installer --backup      # create a backup now
sudo /opt/supremely/installer --restore FILE  # restore from a backup archive
sudo /opt/supremely/installer --rollback    # go back to the previous image
sudo /opt/supremely/installer --uninstall   # remove Supremely and its data
```

Add `--verbose` to any of them to see each step.

`--reset` updates and wipes all data without asking first. It exists for
rebuilding a test installation and will destroy a real one.

## Where things live

| Path | What it is |
|---|---|
| `/opt/supremely/data` | Database, uploads, generated secret key, wizard config |
| `/opt/supremely/supremely.env` | Operator configuration, mode 600 |
| `/opt/supremely/backups` | Backup archives, mode 700 |
| `/opt/supremely/Caddyfile` | Generated proxy configuration, copied to `/etc/caddy` |
| `/opt/supremely/installer` | This script |

## Configuration

Edit `/opt/supremely/supremely.env`, then run `--update` to apply it. These are
real environment variables and take precedence over anything set in the setup
wizard.

The file starts with `APP_ENV=production` and `TRUSTED_PROXIES=1`. See the
[environment variable reference](manual.md#environment-variables) for
everything you can set, and the note there about quoting, which is the most
common way to break a working installation.

To move from SQLite to PostgreSQL, uncomment `DATABASE_URL`, point it at your
server, and run `--update`. Nothing migrates your existing data across for
you: take a backup first and plan the move.

## Updates

Updates are deliberately manual. Run `--update` when you want one.

A health watchdog runs every minute through a systemd timer and restarts a
container that has stopped or gone unhealthy. It only restarts things. It
never fetches or applies updates, because the data directory is writable by
the container, and a watchdog that pulled code would turn any file-creation
bug into remote code execution as root.

Before each update the script takes a backup labelled `pre-update`, keeps a
copy of the current image, and then pulls. If the new containers do not become
healthy, it rolls back to the previous image on its own.

`--rollback` reverts the **image only**. Migrations have already run by then,
so rolling back an hour later leaves old code against a newer schema, which is
not a supported combination. Immediately after a failed update it is the right
tool. Later than that, restore the `pre-update` backup instead.

## Backups

`--backup` stops the worker, takes a consistent SQLite snapshot with
`sqlite3 .backup` rather than copying a file that is being written, archives
the data directory and the environment file, and restarts the worker. The ten
most recent archives are kept.

Archives contain your secret key and, if you use PostgreSQL, your database
password. They are written mode 600 in a directory that is mode 700. Treat
copies you move elsewhere the same way.

If you use PostgreSQL, this backs up uploads and configuration but **not** your
database. Back that up with your provider's tooling or `pg_dump`.

## Logs

```bash
docker logs -f supremely           # application
docker logs -f supremely-worker    # background jobs
journalctl -u caddy -f             # proxy and TLS
cat /var/log/supremely-watchdog.log
```
