"""Constants for the Fröling Lambdatronic S3100 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "froeling_s3100"
MANUFACTURER: Final = "Fröling"
MODEL: Final = "Lambdatronic S3100"

DEFAULT_PORT: Final = 4196
DEFAULT_NAME: Final = "Fröling S3100"

CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_ENABLE_WRITES: Final = "enable_writes"

DEFAULT_UPDATE_INTERVAL: Final = 10
MIN_UPDATE_INTERVAL: Final = 1
MAX_UPDATE_INTERVAL: Final = 300

# Loading the catalog takes about 30 s at 9600 baud.
SETUP_TIMEOUT: Final = 120
ONLINE_TIMEOUT: Final = 30
PROBE_TIMEOUT: Final = 20

STORAGE_VERSION: Final = 1
EVENT_FAULT: Final = "fault"
