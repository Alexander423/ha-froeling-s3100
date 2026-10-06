"""Config flow for the Fröling Lambdatronic S3100."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ENABLE_WRITES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    PROBE_TIMEOUT,
)
from .coordinator import FroelingConfigEntry
from .s3100 import S3100ConnectionError, S3100TimeoutError, async_probe

_LOGGER = logging.getLogger(__name__)

CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): selector.TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
        ),
    }
)


async def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    """Check that the controller answers. Returns form errors."""
    try:
        await async_probe(user_input[CONF_HOST], user_input[CONF_PORT], timeout=PROBE_TIMEOUT)
    except S3100ConnectionError:
        return {"base": "cannot_connect"}
    except S3100TimeoutError:
        return {"base": "no_response"}
    except Exception:
        _LOGGER.exception("Unexpected error while probing the S3100")
        return {"base": "unknown"}
    return {}


def _normalize(user_input: dict[str, Any]) -> dict[str, Any]:
    return {CONF_HOST: user_input[CONF_HOST].strip(), CONF_PORT: int(user_input[CONF_PORT])}


class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the S3100."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the address of the serial bridge."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _normalize(user_input)
            self._async_abort_entries_match(user_input)
            errors = await _validate(user_input)
            if not errors:
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the address of the serial bridge."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _normalize(user_input)
            for other in self._async_current_entries(include_ignore=False):
                if other.entry_id != entry.entry_id and dict(other.data) == user_input:
                    return self.async_abort(reason="already_configured")
            errors = await _validate(user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: FroelingConfigEntry) -> FroelingOptionsFlow:
        """Return the options flow."""
        return FroelingOptionsFlow()


class FroelingOptionsFlow(OptionsFlow):
    """Options: update interval and (experimental) parameter writes."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    CONF_ENABLE_WRITES: user_input[CONF_ENABLE_WRITES],
                }
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL,
                        max=MAX_UPDATE_INTERVAL,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_ENABLE_WRITES, default=False): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )
