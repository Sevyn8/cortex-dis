"""Unit tests for trace_id generation and context-local access."""

from __future__ import annotations

from uuid import UUID

import pytest

from dis_core.trace_id import (
    TraceIdNotSetError,
    bind_trace_id,
    get_trace_id,
    new_trace_id,
    reset_trace_id,
)


def test_new_trace_id_is_uuid7() -> None:
    tid = new_trace_id()
    assert isinstance(tid, UUID)
    assert tid.version == 7


def test_get_raises_when_unbound() -> None:
    with pytest.raises(TraceIdNotSetError):
        get_trace_id()


def test_bind_get_reset_roundtrip() -> None:
    tid = new_trace_id()
    token = bind_trace_id(tid)
    try:
        assert get_trace_id() == tid
    finally:
        reset_trace_id(token)
    with pytest.raises(TraceIdNotSetError):
        get_trace_id()
