"""Asynchronous client for the Fröling Lambdatronic S3100 heating controller."""

from .catalog import build_catalog
from .client import (
    DEFAULT_PORT,
    ConnectionState,
    Event,
    EventType,
    LoginLevel,
    S3100Client,
    UnknownFrame,
    async_probe,
)
from .exceptions import (
    S3100ConnectionError,
    S3100Error,
    S3100NotReadyError,
    S3100ProtocolError,
    S3100TimeoutError,
    S3100WriteError,
    S3100WriteNotAllowedError,
)
from .models import (
    Catalog,
    FaultEvent,
    Measurement,
    MeasurementKind,
    Parameter,
    ParameterKind,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_PORT",
    "Catalog",
    "ConnectionState",
    "Event",
    "EventType",
    "FaultEvent",
    "LoginLevel",
    "Measurement",
    "MeasurementKind",
    "Parameter",
    "ParameterKind",
    "S3100Client",
    "S3100ConnectionError",
    "S3100Error",
    "S3100NotReadyError",
    "S3100ProtocolError",
    "S3100TimeoutError",
    "S3100WriteError",
    "S3100WriteNotAllowedError",
    "UnknownFrame",
    "async_probe",
    "build_catalog",
]
