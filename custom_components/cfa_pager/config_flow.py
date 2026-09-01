"""Config and options flows, so everything is set up from the web interface.

Brigades are entered as station names, alphacodes or raw capcodes and resolved against the
bundled lookup, with anything unresolvable reported in the form rather than silently
ignored. A brigade that never pages you is the worst failure this integration has.

Changing options reloads the entry, so capcode changes take effect without a restart.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import lookup
from .mqtt_test import RESULT_OK, test_connection
from .const import (
    CONF_BRIGADES,
    CONF_BROKER,
    CONF_CLIENT_ID,
    CONF_DEDUPE_SECONDS,
    CONF_HISTORY,
    CONF_PAGE_HISTORY,
    CONF_PORT,
    CONF_TOPICS,
    DEFAULT_BROKER,
    DEFAULT_CLIENT_ID,
    DEFAULT_DEDUPE_SECONDS,
    DEFAULT_HISTORY,
    DEFAULT_PAGE_HISTORY,
    DEFAULT_PORT,
    DEFAULT_TLS,
    DEFAULT_TOPICS,
    DOMAIN,
    CONF_TLS,
    CONF_TLS_INSECURE,
)

TEXT_LIST = selector.TextSelector(selector.TextSelectorConfig(multiple=True))
PASSWORD = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def _connection_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_BROKER, default=defaults.get(CONF_BROKER, DEFAULT_BROKER)): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(
                CONF_CLIENT_ID, default=defaults.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID)
            ): str,
            vol.Optional(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Optional(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): PASSWORD,
            vol.Optional(CONF_TLS, default=defaults.get(CONF_TLS, DEFAULT_TLS)): bool,
            vol.Optional(
                CONF_TLS_INSECURE, default=defaults.get(CONF_TLS_INSECURE, False)
            ): bool,
            vol.Required(CONF_TOPICS, default=defaults.get(CONF_TOPICS, DEFAULT_TOPICS)): TEXT_LIST,
            vol.Required(CONF_BRIGADES, default=defaults.get(CONF_BRIGADES, [])): TEXT_LIST,
        }
    )


def _tuning_schema(defaults: dict) -> dict:
    return {
        vol.Required(
            CONF_DEDUPE_SECONDS,
            default=defaults.get(CONF_DEDUPE_SECONDS, DEFAULT_DEDUPE_SECONDS),
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=3600)),
        vol.Required(
            CONF_HISTORY, default=defaults.get(CONF_HISTORY, DEFAULT_HISTORY)
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
        vol.Required(
            CONF_PAGE_HISTORY,
            default=defaults.get(CONF_PAGE_HISTORY, DEFAULT_PAGE_HISTORY),
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=500)),
    }


def _check_brigades(entries: list[str]) -> tuple[dict, str | None]:
    """Resolve the brigade list, returning ({capcode: label}, error placeholder)."""
    resolved, unresolved = lookup.resolve_many(entries)
    if unresolved:
        return resolved, ", ".join(unresolved)
    if not resolved:
        return resolved, "empty"
    return resolved, None


async def _probe(hass, settings: dict) -> str:
    """Try the broker with these settings and return an mqtt_test RESULT_ constant."""
    return await hass.async_add_executor_job(
        test_connection,
        settings[CONF_BROKER],
        settings[CONF_PORT],
        settings.get(CONF_CLIENT_ID) or DEFAULT_CLIENT_ID,
        settings.get(CONF_USERNAME) or None,
        settings.get(CONF_PASSWORD) or None,
        bool(settings.get(CONF_TLS)),
        bool(settings.get(CONF_TLS_INSECURE)),
    )


class CfaPagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup from the UI, or imported from YAML on first run."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            resolved, problem = _check_brigades(user_input.get(CONF_BRIGADES, []))
            if problem == "empty":
                errors[CONF_BRIGADES] = "no_brigades"
            elif problem:
                errors[CONF_BRIGADES] = "unresolved"
                placeholders["unresolved"] = problem
            if not errors:
                result = await _probe(self.hass, user_input)
                if result != RESULT_OK:
                    errors["base"] = result
            if not errors:
                await self.async_set_unique_id(
                    f"{user_input[CONF_BROKER]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"CFA Pager ({len(resolved)} brigades)",
                    data={
                        CONF_BROKER: user_input[CONF_BROKER],
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_CLIENT_ID: user_input[CONF_CLIENT_ID],
                    },
                    options={
                        CONF_BROKER: user_input[CONF_BROKER],
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_CLIENT_ID: user_input[CONF_CLIENT_ID],
                        CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                        CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                        CONF_TLS: user_input.get(CONF_TLS, DEFAULT_TLS),
                        CONF_TLS_INSECURE: user_input.get(CONF_TLS_INSECURE, False),
                        CONF_TOPICS: user_input[CONF_TOPICS],
                        CONF_BRIGADES: user_input[CONF_BRIGADES],
                        CONF_DEDUPE_SECONDS: DEFAULT_DEDUPE_SECONDS,
                        CONF_HISTORY: DEFAULT_HISTORY,
                        CONF_PAGE_HISTORY: DEFAULT_PAGE_HISTORY,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Migrate an existing YAML configuration into a config entry."""
        await self.async_set_unique_id(
            f"{import_data[CONF_BROKER]}:{import_data[CONF_PORT]}"
        )
        self._abort_if_unique_id_configured()
        brigades = import_data.get(CONF_BRIGADES) or import_data.get("capcodes") or []
        resolved, _ = lookup.resolve_many(brigades)
        return self.async_create_entry(
            title=f"CFA Pager ({len(resolved)} brigades)",
            data={
                CONF_BROKER: import_data[CONF_BROKER],
                CONF_PORT: import_data[CONF_PORT],
                CONF_CLIENT_ID: import_data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
            },
            options={
                CONF_BROKER: import_data[CONF_BROKER],
                CONF_PORT: import_data[CONF_PORT],
                CONF_CLIENT_ID: import_data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
                CONF_USERNAME: import_data.get(CONF_USERNAME, ""),
                CONF_PASSWORD: import_data.get(CONF_PASSWORD, ""),
                CONF_TLS: import_data.get(CONF_TLS, DEFAULT_TLS),
                CONF_TLS_INSECURE: import_data.get(CONF_TLS_INSECURE, False),
                CONF_TOPICS: import_data.get(CONF_TOPICS, DEFAULT_TOPICS),
                CONF_BRIGADES: list(brigades),
                CONF_DEDUPE_SECONDS: import_data.get(
                    CONF_DEDUPE_SECONDS, DEFAULT_DEDUPE_SECONDS
                ),
                CONF_HISTORY: import_data.get(CONF_HISTORY, DEFAULT_HISTORY),
                CONF_PAGE_HISTORY: import_data.get(
                    CONF_PAGE_HISTORY, DEFAULT_PAGE_HISTORY
                ),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return CfaPagerOptionsFlow()


class CfaPagerOptionsFlow(OptionsFlow):
    """Change brigades and tuning after setup. Saving reloads the entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            resolved, problem = _check_brigades(user_input.get(CONF_BRIGADES, []))
            if problem == "empty":
                errors[CONF_BRIGADES] = "no_brigades"
            elif problem:
                errors[CONF_BRIGADES] = "unresolved"
                placeholders["unresolved"] = problem
            if not errors:
                result = await _probe(self.hass, {**current, **user_input})
                if result != RESULT_OK:
                    errors["base"] = result
            if not errors:
                return self.async_create_entry(data=user_input)
            current = {**current, **user_input}

        schema = _connection_schema(current).extend(
            {
                vol.Required(
                    CONF_TOPICS, default=current.get(CONF_TOPICS, DEFAULT_TOPICS)
                ): TEXT_LIST,
                **_tuning_schema(current),
            }
        )
        resolved_now, _ = lookup.resolve_many(current.get(CONF_BRIGADES, []))
        placeholders.setdefault(
            "resolved",
            ", ".join(f"{label} ({code})" for code, label in sorted(resolved_now.items()))
            or "none",
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )
