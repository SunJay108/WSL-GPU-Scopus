"""
Manage search UI accounts from the command line (e.g. a forgotten admin password).

    docker exec -it scopus-ai-gpu python users.py list
    docker exec -it scopus-ai-gpu python users.py create-admin NAME
    docker exec -it scopus-ai-gpu python users.py reset-password NAME
    docker exec -it scopus-ai-gpu python users.py set-role NAME admin|user
    docker exec -it scopus-ai-gpu python users.py enable NAME
"""

import argparse
import getpass
import sys
import time

import audit
import auth
import db


def _ask_password():
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return first


def _user(name):
    user = auth.get_user_by_name(name)
    if user is None:
        sys.exit(f"No user named {name!r}.")
    return user


def main():
    parser = argparse.ArgumentParser(description="Manage Scopus AI accounts.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("create-admin", "reset-password", "enable"):
        sub.add_parser(name).add_argument("username")
    role = sub.add_parser("set-role")
    role.add_argument("username")
    role.add_argument("role", choices=auth.ROLES)
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
            audit.log("admin_create_user", cli, detail={"target": user["username"], "role": "admin"})
            print(f"Administrator {user['username']!r} created.")
        elif args.command == "reset-password":
            user = _user(args.username)
            auth.set_password(user["id"], _ask_password())
            auth.delete_user_sessions(user["id"])
            audit.log("admin_reset_password", cli, detail={"target": user["username"]})
            print("Password changed.")
        elif args.command == "set-role":
            user = _user(args.username)
            auth.set_role(user["id"], args.role)
            audit.log("admin_set_role", cli, detail={"target": user["username"], "role": args.role,
                                                     "previous": user["role"]})
            print(f"{user['username']!r} is now {args.role}.")
        elif args.command == "enable":
            user = _user(args.username)
            auth.set_disabled(user["id"], False)
            audit.log("admin_enable_user", cli, detail={"target": user["username"]})
            print(f"{user['username']!r} enabled.")
    except auth.AuthError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
