#!/usr/bin/env python3
"""Manage Kraven profiles (lightweight; <=15 expected).

  python users.py add <username> <password>     # create or reset password
  python users.py list
  python users.py delete <username>
  python users.py migrate <username>            # move the legacy singleton
                                                # resume + scores into this profile
"""
from __future__ import annotations

import datetime
import sys

import db


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main() -> None:
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "add" and len(a) >= 3:
        db.create_user(a[1], a[2], now=_now())
        print(f"profile '{a[1]}' created/updated")
    elif cmd == "list":
        for u in db.list_users():
            r = db.get_resume(u)
            print(f"  {u:20s} resume: {r['filename'] if r else '(none)'}")
    elif cmd == "delete" and len(a) >= 2:
        db.delete_user(a[1])
        print(f"deleted '{a[1]}'")
    elif cmd == "migrate" and len(a) >= 2:
        db.migrate_singleton_to(a[1])
        print(f"migrated legacy resume + scores into '{a[1]}'")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
