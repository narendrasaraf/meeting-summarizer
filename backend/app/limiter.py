"""
Shared rate-limiter instance.

Defined here (not in main.py or meetings.py) so that both modules can import
the same object without creating a circular import.  The limiter is attached to
app.state in app.main for the slowapi exception handler; the same instance is
used by the @limiter.limit() decorator in meetings.py.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])
