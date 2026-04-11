"""User context helpers for shared application code.

Provides the current user's identity to shared Python code (skills, tools,
library functions) so they can scope DB queries and actions to the active user.

Usage in shared code:

    from core.user_context import current_user_id

    def get_portfolios(db):
        uid = current_user_id()  # raises if no user context
        return db.execute(
            "SELECT * FROM portfolios WHERE user_id = :uid",
            {"uid": uid}
        )

    # Or with the optional variant:
    from core.user_context import current_user_id_or_none

    uid = current_user_id_or_none()  # returns None if no user context
"""

from __future__ import annotations


def current_user_id() -> str:
    """Get the current user's ID. Raises PermissionError if not in a user session.

    Use this in shared code to scope all DB queries and actions to the
    active user. This ensures multi-user isolation at the application level.
    """
    from core.session import current_session_get
    uid = current_session_get("user_id")
    if not uid or uid == "default":
        raise PermissionError(
            "No authenticated user context. "
            "This code must run within a user session."
        )
    return uid


def current_user_id_or_none() -> str | None:
    """Get the current user's ID, or None if not in a user session.

    Use this when the code can optionally scope to a user (e.g., shared
    resources that fall back to global when no user is present).
    """
    from core.session import current_session_get
    uid = current_session_get("user_id")
    if not uid or uid == "default":
        return None
    return uid


def is_admin() -> bool:
    """Check if the current session user has admin privileges."""
    from core.session import current_session_get
    return bool(current_session_get("is_admin"))
