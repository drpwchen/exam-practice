"""Single global Limiter instance — imported by main.py (registration) and
routers (decorators). Kept in its own module to avoid circular imports."""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
