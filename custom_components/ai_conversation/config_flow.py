"""Config flow for Conversation integration."""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any

import openai
from openai.types.chat import (
    ChatCompletionUserMessageParam,
)
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_BASE, CONF_SERVICE, CONF_API_KEY, CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.util import ulid
from homeassistant.helpers import llm
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TemplateSelector,
)

from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DOMAIN,
    RECOMMENDED_CHAT_MODEL,
    RECOMMENDED_MAX_TOKENS,
    RECOMMENDED_TEMPERATURE,
    RECOMMENDED_TOP_P,
)

_LOGGER = logging.getLogger(__name__)

RECOMMENDED_OPTIONS = {
    CONF_LLM_HASS_API: llm.LLM_API_ASSIST,
    CONF_PROMPT: llm.DEFAULT_INSTRUCTIONS_PROMPT,
}

OPENAI_API = 'https://api.openai.com/v1'
ZHI_PU_API = 'https://open.bigmodel.cn/api/paas/v4'
SERVICES = {
    OPENAI_API: {
        'name': 'OpenAI',
        'model': RECOMMENDED_CHAT_MODEL,
    },
    ZHI_PU_API: {
        'name': '智谱AI',
        'model': 'glm-4-flash',
    },
}

OPEN_APIS = {
    k: v['name']
    for k, v in SERVICES.items()
}


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate the user input allows us to connect."""
    url = data.get(CONF_SERVICE, OPENAI_API)
    url = data.get(CONF_BASE, '').replace('/chat/completions', '') or url
    key = data.get(CONF_API_KEY, '')

    if not key and url == ZHI_PU_API:
        lnk = 'https://www.bigmodel.cn/invite?icode=EwilDKx13%2FhyODIyL%2BKabHHEaazDlIZGj9HxftzTbt4%3D'
        raise ValueError(f'智谱AI为用户提供了免费的大模型，[立即注册]({lnk})免费使用！')

    model = data.get(CONF_CHAT_MODEL) or SERVICES.get(url, {}).get('model', RECOMMENDED_CHAT_MODEL)
    data[CONF_BASE] = url
    data[CONF_CHAT_MODEL] = model
    client = openai.AsyncOpenAI(api_key=key, base_url=url)
    messages = [ChatCompletionUserMessageParam(role='user', content='hello')]
    await client.chat.completions.create(
        model=model,
        messages=messages,
        user=ulid.ulid_now(),
    )


class OpenAIConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Conversation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors = {}
        dat = user_input or {}
        if dat:
            try:
                await validate_input(self.hass, dat)
            except openai.APIConnectionError:
                errors["base"] = "cannot_connect"
            except openai.AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception as exc:
                self.context['tip'] = f'⚠️ {exc}'
            else:
                dat.pop(CONF_SERVICE, None)
                model = dat.pop(CONF_CHAT_MODEL, RECOMMENDED_CHAT_MODEL)
                domain = dat.get(CONF_BASE, '')
                domain = f'{domain}//'.split('/')[2]
                return self.async_create_entry(
                    title=domain or 'ChatGPT',
                    data=dat,
                    options={
                        **RECOMMENDED_OPTIONS,
                        CONF_CHAT_MODEL: model,
                    },
                )

        schema = vol.Schema(
            {
                vol.Optional(CONF_SERVICE, default=dat.get(CONF_SERVICE, OPENAI_API)): vol.In(OPEN_APIS),
                vol.Optional(CONF_BASE, default=dat.get(CONF_BASE, '')): str,
                vol.Optional(CONF_API_KEY, default=dat.get(CONF_API_KEY, '')): str,
                vol.Optional(CONF_CHAT_MODEL, default=dat.get(CONF_CHAT_MODEL, '')): str,
            }
        )
        return self.async_show_form(
            step_id='user', data_schema=schema, errors=errors,
            description_placeholders={'tip': self.context.pop('tip', '')},
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return OpenAIOptionsFlow(config_entry)


class OpenAIOptionsFlow(OptionsFlow):
    """OpenAI config flow options handler."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        options: dict[str, Any] | MappingProxyType[str, Any] = self.config_entry.options
        options = {**options, **self.config_entry.data}

        if user_input is not None:
            options = {**user_input}
            data = {
                CONF_BASE: user_input.pop(CONF_BASE),
                CONF_API_KEY: user_input.pop(CONF_API_KEY),
            }
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            return self.async_create_entry(title="", data=user_input)

        schema = openai_config_option_schema(self.hass, options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
        )


def openai_config_option_schema(
    hass: HomeAssistant,
    options: dict[str, Any] | MappingProxyType[str, Any],
) -> dict:
    """Return a schema for OpenAI completion options."""
    hass_apis: list[SelectOptionDict] = [
        SelectOptionDict(
            label="No control",
            value="none",
        )
    ]
    hass_apis.extend(
        SelectOptionDict(
            label=api.name,
            value=api.id,
        )
        for api in llm.async_get_apis(hass)
    )

    schema = {
        vol.Required(
            CONF_BASE,
            description={"suggested_value": options.get(CONF_BASE)},
        ): str,
        vol.Optional(
            CONF_API_KEY,
            description={"suggested_value": options.get(CONF_API_KEY)},
        ): str,
        vol.Optional(
            CONF_PROMPT,
            description={"suggested_value": options.get(CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT)},
        ): TemplateSelector(),
        vol.Optional(
            CONF_LLM_HASS_API,
            description={"suggested_value": options.get(CONF_LLM_HASS_API)},
            default="none",
        ): SelectSelector(SelectSelectorConfig(options=hass_apis)),
        vol.Optional(
            CONF_CHAT_MODEL,
            description={"suggested_value": options.get(CONF_CHAT_MODEL)},
            default=RECOMMENDED_CHAT_MODEL,
        ): str,
        vol.Optional(
            CONF_MAX_TOKENS,
            description={"suggested_value": options.get(CONF_MAX_TOKENS)},
            default=RECOMMENDED_MAX_TOKENS,
        ): int,
        vol.Optional(
            CONF_TOP_P,
            description={"suggested_value": options.get(CONF_TOP_P)},
            default=RECOMMENDED_TOP_P,
        ): NumberSelector(NumberSelectorConfig(min=0, max=1, step=0.05)),
        vol.Optional(
            CONF_TEMPERATURE,
            description={"suggested_value": options.get(CONF_TEMPERATURE)},
            default=RECOMMENDED_TEMPERATURE,
        ): NumberSelector(NumberSelectorConfig(min=0, max=2, step=0.05)),
    }

    return schema
