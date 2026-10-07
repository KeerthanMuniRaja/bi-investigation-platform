"""Error types shared across layers (store, services, connectors)."""
from __future__ import annotations


class NotFoundError(KeyError):
    """A requested object doesn't exist. The API turns this into a 404 with
    the message; any other KeyError is a programming error (500)."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "not found"
