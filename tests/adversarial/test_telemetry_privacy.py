import io
import json
import logging

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from um_agentic.security.telemetry import PHIScrubbingProcessor, SafeJSONFormatter


def test_zero_phi_in_exported_spans_and_structured_logs() -> None:
    sensitive = "Synthetic Jane Example SSN 999-12-3456 member 7654321"
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(PHIScrubbingProcessor(exporter))
    with provider.get_tracer("synthetic-test").start_as_current_span(sensitive) as span:
        span.set_attribute("document.text", sensitive)
        span.set_attribute("llm.prompt", sensitive)
        span.set_attribute("um.member_token", sensitive)
        span.set_attribute("um.input_tokens", 42)
        span.add_event(sensitive, {"clinical_note": sensitive})
        span.set_status(Status(StatusCode.ERROR, sensitive))
    provider.force_flush()
    exported = exporter.get_finished_spans()
    serialized = "\n".join(span.to_json() for span in exported)
    assert sensitive not in serialized
    assert "999-12-3456" not in serialized
    assert "llm.prompt" not in serialized
    assert exported[0].attributes == {"um.input_tokens": 42}
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJSONFormatter())
    logger = logging.getLogger("synthetic-privacy-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("%s", sensitive, extra={"safe_attributes": {"clinical_note": sensitive, "um.input_tokens": 42}})
    assert sensitive not in stream.getvalue()
    assert json.loads(stream.getvalue())["um.input_tokens"] == 42
    provider.shutdown()
