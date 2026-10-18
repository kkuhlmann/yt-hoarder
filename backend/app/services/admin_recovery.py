"""Filesystem drop for admin account recovery codes.

An admin who has lost their password proves control of the *host machine* rather than
of an email inbox: the recovery code is written to a file under the /data bind mount,
so retrieving it requires access to the server yt-hoarder runs on.

The file holds a single-use code, never a password that has already been applied. If it
carried a live credential, any anonymous caller could reset the admin's account on demand
and lock them out; with a code, the account is untouched until someone who actually read
the file submits it.
"""

import os
import tempfile
from datetime import datetime

from logger import logger

RECOVERY_FILE_PATH = '/data/admin-recovery.txt'

_TEMPLATE = """yt-hoarder admin account recovery
===================================

  user:    {username}
  code:    {code}
  expires: {expires} UTC

Enter this code on the "Admin account recovery" screen to choose a new password.
It can be used once. This file is deleted as soon as it is used, and the code stops
working at the expiry above whether or not it was used.

If you did not request this, someone with network access to yt-hoarder did. They
cannot do anything without this file, so deleting it is enough.
"""


def write_recovery_file(username: str, code: str, expires_at: datetime) -> str:
    """Write the recovery code to RECOVERY_FILE_PATH, replacing any previous one.

    Raises PermissionError if the /data mount is not writable by the app user; callers
    surface that as an actionable message rather than a generic 500.
    """
    directory = os.path.dirname(RECOVERY_FILE_PATH)
    os.makedirs(directory, exist_ok=True)

    content = _TEMPLATE.format(
        username=username,
        code=code,
        expires=expires_at.strftime('%Y-%m-%d %H:%M:%S'),
    )

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.tmp', dir=directory)
        with os.fdopen(fd, 'w') as f:
            f.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, RECOVERY_FILE_PATH)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    # The code itself is never logged — that would defeat the point of requiring
    # filesystem access to obtain it.
    logger.info(f'Wrote admin recovery code for {username} to {RECOVERY_FILE_PATH}')
    return RECOVERY_FILE_PATH


def delete_recovery_file() -> None:
    try:
        os.unlink(RECOVERY_FILE_PATH)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f'Could not remove {RECOVERY_FILE_PATH}: {e}')
