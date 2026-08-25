"""Runtime-written configuration on the data volume.

The setup wizard runs after boot, so its answers cannot come from the
environment. They are written to $DATA_DIR/config.env and layered under real
environment variables at boot (env always wins). See
blueprint/patterns/core/deployment.md § Runtime-written configuration.
"""

import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values


def runtime_config_path(app) -> Path:
    return Path(app.config['DATA_DIR']) / 'config.env'


def read_runtime_config(app) -> dict:
    path = runtime_config_path(app)
    if not path.exists():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def write_runtime_config(app, updates: dict) -> None:
    """Merge updates into config.env, atomically."""
    path = runtime_config_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = read_runtime_config(app)
    merged.update({k: str(v) for k, v in updates.items()})

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.config.env.')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write('# Written by the Supremely setup wizard. Environment variables override.\n')
            for key, value in merged.items():
                f.write(f'{key}={value}\n')
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def installation_ready(app) -> bool:
    """True once the setup wizard has completed.

    Cached per process once True. While False, re-reads config.env each call
    so every Gunicorn worker notices when another worker finishes the wizard.
    """
    if app.config.get('SETUP_COMPLETE'):
        return True
    value = read_runtime_config(app).get('SETUP_COMPLETE', '')
    if value.strip().lower() == 'true':
        app.config['SETUP_COMPLETE'] = True
        return True
    return False


def mark_installed(app) -> None:
    write_runtime_config(app, {'SETUP_COMPLETE': 'true'})
    app.config['SETUP_COMPLETE'] = True
