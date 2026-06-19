"""Reset a user's password from the CLI.

Usage (single-quote password to avoid shell history expansion of !):
    python -m tools.reset_admin_pw '<username>' '<new_password>'

Effects:
    - Replaces password_hash with bcrypt hash of new_password
    - Sets must_change_password=False (admin presumably knows what they typed)
    - Existing JWTs remain valid until expiry; only future logins use new password
"""

import sys

from sqlalchemy import select

from backend.auth import hash_password
from backend.db import SessionLocal
from backend.models import User


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    username, new_password = sys.argv[1], sys.argv[2]
    if len(new_password) < 8:
        print(f"refusing: password too short ({len(new_password)} chars, need >=8)", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            print(f"no such user: {username}", file=sys.stderr)
            return 1
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        db.commit()
        print(f"ok: reset password for {username} (id={user.id}, is_admin={user.is_admin})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
