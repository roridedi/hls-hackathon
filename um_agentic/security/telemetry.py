import hashlib
import json
import logging
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Status

SAFE_TEXT = frozenset({
    "um.agent", "um.model_id", "um.prompt_version", "um.criteria_version", "um.status",
    "um.outcome", "service.name", "http.request.method",
})
SAFE_NUMBERS = frozenset({
    "um.latency_ms", "um.input_tokens", "um.output_tokens", "um.percent_elapsed",
    "http.response.status_code",
})
SAFE_EVENTS = frozenset({
    "agent_completed", "agent_failed", "outbox_failed", "outbox_delivered",
    "clock_warning", "service_started", "analytics_completed",
})


def safe_attributes(attributes: Mapping[str, Any] | None) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for key, value in (attributes or {}).items():
        if key in SAFE_NUMBERS and isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = value
        elif key in SAFE_TEXT and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/ -]{1,120}", value):
            if key == "um.agent" and value not in {
                "clinical_evidence", "documentation", "criteria", "verifier", "recommendation",
                "reviewer_copilot", "letter", "insights", "coverage_check", "intake_normalizer",
            }:
                continue
            result[key] = value
        elif key == "um.case_id":
            try:
                result[key] = str(UUID(str(value)))
            except ValueError:
                continue
        elif key == "um.inputs_hash" and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
            result[key] = value
        elif key == "um.member_token" and isinstance(value, str) and re.fullmatch(r"(?:member_|tok_)[A-Za-z0-9_]{3,100}", value):
            result[key] = "tok_" + hashlib.sha256(value.encode()).hexdigest()[:24]
    return result


class PHIScrubbingProcessor(SpanProcessor):
    def __init__(self, exporter: SpanExporter) -> None:
        self.delegate = BatchSpanProcessor(exporter)

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        self.delegate.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        self.delegate.on_end(ReadableSpan(
            name=span.name if span.name in {"um.agent.run", "um.case.process", "um.request"} else "um.operation",
            context=span.context, parent=span.parent,
            resource=Resource({"service.name": "um-agentic"}),
            attributes=safe_attributes(span.attributes),
            events=(), links=(), kind=span.kind,
            status=Status(span.status.status_code),
            start_time=span.start_time, end_time=span.end_time,
        ))

    def shutdown(self) -> None:
        self.delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.delegate.force_flush(timeout_millis)


class SafeJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = record.msg if isinstance(record.msg, str) and record.msg in SAFE_EVENTS else "application_event"
        attributes = safe_attributes(getattr(record, "safe_attributes", {}))
        return json.dumps({"event": event, "level": record.levelname, **attributes}, allow_nan=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("uvicorn.access", "uvicorn.error", "azure", "agent_framework", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
        logger.setLevel(logging.WARNING)


def configure_telemetry(
    otlp_endpoint: str | None = None, app_insights_connection_string: str | None = None,
) -> TracerProvider:
    provider = TracerProvider(resource=Resource({"service.name": "um-agentic"}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(PHIScrubbingProcessor(OTLPSpanExporter(
            endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces",
        )))
        metrics.set_meter_provider(MeterProvider(
            resource=Resource({"service.name": "um-agentic"}),
            metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(
                endpoint=f"{otlp_endpoint.rstrip('/')}/v1/metrics",
            ))],
        ))
    elif app_insights_connection_string:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

        provider.add_span_processor(PHIScrubbingProcessor(AzureMonitorTraceExporter(
            connection_string=app_insights_connection_string,
            credential=DefaultAzureCredential(),
            disable_offline_storage=True,
        )))
    trace.set_tracer_provider(provider)
    return provider


def record_agent_metrics(agent: str, model: str, latency: float, input_tokens: int, output_tokens: int) -> None:
    attributes = {"um.agent": agent, "um.model_id": model}
    meter = metrics.get_meter("um_agentic")
    meter.create_histogram("um.agent.latency", unit="ms").record(latency, attributes)
    meter.create_counter("um.agent.input_tokens").add(input_tokens, attributes)
    meter.create_counter("um.agent.output_tokens").add(output_tokens, attributes)
