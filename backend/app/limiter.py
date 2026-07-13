"""
Shared rate-limiter instance.

Defined here (not in main.py or meetings.py) so that both modules can import
the same object without creating a circular import.  The limiter is attached to
app.state in app.main for the slowapi exception handler; the same instance is
used by the @limiter.limit() decorator in meetings.py.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def get_rate_limit_key(request: Request) -> str:
    # Read user from request state if set by get_current_user
    user = getattr(request.state, "user", None)
    if user and hasattr(user, "id") and user.id is not None:
        return f"user:{user.id}"
    return get_remote_address(request)


limiter = Limiter(key_func=get_rate_limit_key, default_limits=[])
