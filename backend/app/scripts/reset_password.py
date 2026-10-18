"""Reset a user's password from a shell on the host.

The last-resort recovery path: it needs no running web session and no working admin
account, only shell access to the container.

    docker compose exec backend python -m scripts.reset_password <username>

or, from the repo root, `task admin:reset-password -- <username>`.

This lives under backend/app/ because the production image copies only that
directory into /app — a script anywhere else in backend/ is reachable in dev and
silently absent in prod.
"""

import argparse
import getpass
import sys

from auth import hash_password
from database import db
from models import MIN_PASSWORD_LENGTH
from repositories import users as user_repo


def _prompt_for_password() -> str | None:
    password = getpass.getpass('New password: ')
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f'Password must be at least {MIN_PASSWORD_LENGTH} characters.', file=sys.stderr)
        return None
    if password != getpass.getpass('Confirm password: '):
        print('Passwords do not match.', file=sys.stderr)
        return None
    return password


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset a yt-hoarder user's password.")
    parser.add_argument('username')
    args = parser.parse_args()

    db.initialize_database()

    user = user_repo.sync_get_user_by_username(args.username)
    if not user:
        print(f"No user named '{args.username}'.", file=sys.stderr)
        return 1

    password = _prompt_for_password()
    if password is None:
        return 1

    if not user_repo.sync_set_password(user.id, hash_password(password)):
        print(f"Failed to update '{args.username}'.", file=sys.stderr)
        return 1

    print(f"Password updated for '{user.username}'. Other sessions have been signed out.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
