"""
Manage the administrator accounts of the embedding service from the command
line (add an administrator, reset a forgotten password).

    docker exec -it embed-service python users.py list
    docker exec -it embed-service python users.py create-admin NAME
    docker exec -it embed-service python users.py reset-password NAME
"""

import argparse
import getpass
import sys
import time

from core import audit, auth, db


def _ask_password():
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return first


def main():
    parser = argparse.ArgumentParser(description="Manage embedding service accounts.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("create-admin", "reset-password"):
        sub.add_parser(name).add_argument("username")
    args = parser.parse_args()

    db.init()
    cli = {"id": None, "username": "command line"}
    try:
        if args.command == "list":
            for u in auth.list_users():
                last = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_login_at"])) if u["last_login_at"] else "never"
                state = "disabled" if u["disabled"] else "active"
                print(f"{u['username']:20s} {u['role']:6s} {state:9s} last login {last}")
        elif args.command == "create-admin":
            user = auth.create_user(args.username, _ask_password(), role="admin")
            audit.log("admin_create_user", cli, detail={"target": user["username"]})
            print(f"Administrator {user['username']!r} created.")
        else:
            user = auth.get_user_by_name(args.username)
            if user is None:
                sys.exit(f"No user named {args.username!r}.")
            auth.set_password(user["id"], _ask_password())
            auth.delete_user_sessions(user["id"])
            audit.log("admin_reset_password", cli, detail={"target": user["username"]})
            print("Password changed.")
    except auth.AuthError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
