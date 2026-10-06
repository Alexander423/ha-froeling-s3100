"""Exceptions raised by the S3100 client."""


class S3100Error(Exception):
    """Base class for all S3100 errors."""


class S3100ConnectionError(S3100Error):
    """The TCP bridge could not be reached or closed the connection."""


class S3100TimeoutError(S3100Error):
    """The controller stopped answering."""


class S3100ProtocolError(S3100Error):
    """The controller sent data that does not fit the announced layout."""


class S3100NotReadyError(S3100Error):
    """The session is not established yet."""


class S3100WriteError(S3100Error):
    """A parameter write was refused or could not be confirmed."""


class S3100WriteNotAllowedError(S3100WriteError):
    """The parameter is not writable or the value violates its limits."""
