"""Isolated OpenTelemetry export for the log records added by this integration."""
from __future__ import annotations

import logging

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

POSTHOG_LOGGER_NAME = "investwise.posthog_logs"
_EXPORTER_HANDLER_MARKER = "_investwise_posthog_log_exporter"


def configure_posthog_log_export(
    *, endpoint: str, project_token: str, environment: str
) -> LoggerProvider:
    """Export only records emitted through the dedicated PostHog logger."""
    logger = logging.getLogger(POSTHOG_LOGGER_NAME)
    for handler in logger.handlers:
        provider = getattr(handler, _EXPORTER_HANDLER_MARKER, None)
        if provider is not None:
            return provider

    provider = LoggerProvider(
        resource=Resource.create(
            {
                "service.name": "investwise-pro",
                "deployment.environment": environment,
            }
        )
    )
    exporter = OTLPLogExporter(
        endpoint=endpoint,
        headers={"Authorization": f"Bearer {project_token}"},
    )
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))

    handler = LoggingHandler(logger_provider=provider)
    setattr(handler, _EXPORTER_HANDLER_MARKER, provider)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return provider


def shutdown_posthog_log_export(provider: LoggerProvider) -> None:
    """Flush the dedicated exporter during application shutdown only."""
    try:
        provider.force_flush(timeout_millis=5_000)
    finally:
        provider.shutdown()
