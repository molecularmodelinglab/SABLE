import sys

from server.database import get_db_context
from server.services.user_service import user_service

def main(email: str) -> None:
    with get_db_context() as db:
        user = user_service.get_user_by_email(db, email)
        if not user:
            raise SystemExit(f"No user found for email {email}")

        roles = set(user.roles)
        roles.add("admin")
        user_service.update_user(db, user, extra_metadata={"roles": sorted(roles)})
        print(f"{email} is now an admin.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m server.scripts.promote_admin <email>")
    main(sys.argv[1])