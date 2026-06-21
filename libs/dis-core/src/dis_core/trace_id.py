"""trace_id generation and context-local access.

A ``trace_id`` is minted once per ingress chunk at the receiver (architecture §8,
CLAUDE.md hard rule 4) and propagated end-to-end — never regenerated mid-pipeline.
It is a UUIDv7 (``ids.new_uuid7``), matching the ``trace_id uuid NOT NULL`` columns
in canonical/event/audit tables.

The current trace_id is held in a :class:`contextvars.ContextVar` so logging and
audit helpers can read it without threading it through every call. ``bind`` sets
it for the current context (and async task); ``get`` reads it; ``clear`` resets it.
Receivers ``bind`` at chunk entry; ``get`` raises if nothing is bound, so a missing
trace_id fails loudly rather than silently logging ``None``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from uuid import UUID

from dis_core.errors import DisError
from dis_core.ids import new_uuid7

_TRACE_ID: ContextVar[UUID] = ContextVar("dis_trace_id")


class TraceIdNotSetError(DisError):
    """``get_trace_id`` was called with no trace_id bound in the current context."""


def new_trace_id() -> UUID:
    """Mint a fresh trace_id (UUIDv7). Call once per ingress chunk, at the receiver."""
    return new_uuid7()


def bind_trace_id(trace_id: UUID) -> Token[UUID]:
    """Bind ``trace_id`` for the current context. Returns a token for :func:`reset_trace_id`."""
    return _TRACE_ID.set(trace_id)


def get_trace_id() -> UUID:
    """Return the trace_id bound in the current context, or raise :class:`TraceIdNotSetError`."""
    try:
        return _TRACE_ID.get()
    except LookupError as exc:
        raise TraceIdNotSetError("no trace_id bound in the current context") from exc


def reset_trace_id(token: Token[UUID]) -> None:
    """Restore the trace_id context to its state before the matching :func:`bind_trace_id`."""
    _TRACE_ID.reset(token)
