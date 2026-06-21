"""Unit tests for the structured logging helper."""

from __future__ import annotations

import io
import json
import logging

from dis_core.logging import DisLoggerAdapter, get_logger

try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # 2.x (see dis_core.logging — the 3.x stubs don't re-export this name)
    from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore[attr-defined]


def test_get_logger_binds_service() -> None:
    log = get_logger("streaming-consumer")
    assert isinstance(log, DisLoggerAdapter)
    assert log.extra is not None  # LoggerAdapter.extra is Optional; ours always binds it
    assert log.extra["service"] == "streaming-consumer"


def test_bind_adds_context_without_mutating_parent() -> None:
    parent = get_logger("dis-ui-server")
    child = parent.bind(stage="map", tenant_id="t-uuid", trace_id="tr-uuid")
    assert parent.extra is not None and child.extra is not None
    assert "stage" not in parent.extra
    assert child.extra["service"] == "dis-ui-server"
    assert child.extra["stage"] == "map"
    assert child.extra["tenant_id"] == "t-uuid"


def test_context_fields_appear_in_json_record() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("%(message)s"))
    base = logging.getLogger("test.dis.logging")
    base.handlers = [handler]
    base.setLevel(logging.INFO)
    base.propagate = False

    log = DisLoggerAdapter(base, {"service": "csv-ingest-worker", "stage": "preflight"})
    log.info("chunk received", extra={"trace_id": "tr-123"})

    payload = json.loads(stream.getvalue())
    assert payload["service"] == "csv-ingest-worker"
    assert payload["stage"] == "preflight"
    assert payload["trace_id"] == "tr-123"
    assert payload["message"] == "chunk received"
