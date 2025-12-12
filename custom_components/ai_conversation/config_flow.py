from aiohttp import hdrs, client_exceptions, web_exceptions
from homeassistant import config_entries
from homeassistant.helpers import llm
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TemplateSelector,
    ObjectSelector,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import *

OPENAI_API = "https://api.openai.com/v1"
ZHI_PU_API = "https://open.bigmodel.cn/api/paas/v4"
SERVICES = {
    ZHI_PU_API: {
        CONF_NAME: "智谱AI",
        "models": [
            "glm-4.6v-flash", "glm-4.1v-thinking-flash", "glm-4.5-flash", "glm-4-flash-250414", "glm-4v-flash",
            "glm-z1-flash", "cogview-3-flash", "cogvideox-flash",
        ],
    },
    OPENAI_API: {
        CONF_NAME: "OpenAI",
        "models": ["gpt-4o", "gpt-4o-mini"],
    },
    CONF_CUSTOM: {
        CONF_NAME: "Custom (自定义)",
        "models": [""],
    },
}

OPEN_APIS = {
    k: v[CONF_NAME]
    for k, v in SERVICES.items()
}


async def get_models(hass: HomeAssistant, data: dict):
    """Validate the user input allows us to connect."""
    url = data.get(CONF_BASE, "")
    key = data.get(CONF_API_KEY, "")

    if not key and url == ZHI_PU_API:
        lnk = "https://www.bigmodel.cn/invite?icode=EwilDKx13%2FhyODIyL%2BKabHHEaazDlIZGj9HxftzTbt4%3D"
        raise ValueError(f"智谱AI为用户提供了免费的大模型，[立即注册]({lnk})免费使用！")

    session = async_create_clientsession(hass, base_url=f"{url.rstrip('/')}/")
    res = await session.get("models", timeout=10.0, headers={
        hdrs.AUTHORIZATION: f"Bearer {key}",
    })
    resp = await res.json()
    if res.status == 401:
        error = resp.get("error", resp) if isinstance(resp, dict) else resp
        text = error.get("message") or str(error)
        raise web_exceptions.HTTPUnauthorized(body=text)
    LOGGER.info("Got models: %s", [data, resp, res])
    return resp.get("data") or []

class HasAttrs:
    attrs = None

    def get_attr(self, attr, default=None):
        return self.attrs.pop(attr, default) if self.attrs else default

    def set_attr(self, attr, value=None):
        if self.attrs is None:
            self.attrs = {}
        self.attrs[attr] = value

    @property
    def tip(self):
        return self.get_attr("tip", "")

    @tip.setter
    def tip(self, value):
        self.set_attr("tip", value)

class BasicFlow(config_entries.ConfigEntryBaseFlow, HasAttrs):
    hass = None
    config_entry = None

    async def async_step_init(self, user_input=None, step_id="init"):
        if user_input is None:
            user_input = {}
        defaults = {**user_input}
        if self.config_entry:
            defaults = {**self.config_entry.data}
        if service := user_input.pop(CONF_SERVICE, None):
            self.set_attr(CONF_SERVICE, service)
        else:
            service = self.get_attr(CONF_SERVICE) or defaults.get(CONF_BASE)
        defaults.update(user_input)
        if service and service in SERVICES:
            defaults.setdefault(CONF_BASE, service if service != CONF_CUSTOM else "")

        schema = {
            vol.Required(CONF_BASE): str,
            vol.Optional(CONF_API_KEY): str,
        }
        errors = {}

        if base := user_input.get(CONF_BASE):
            base = base.replace("/chat/completions", "")
            user_input[CONF_BASE] = base
            try:
                await get_models(self.hass, user_input)
            except client_exceptions.ClientConnectionError:
                errors["base"] = "cannot_connect"
            except web_exceptions.HTTPUnauthorized as exc:
                errors["base"] = "invalid_auth"
                self.tip = f'🔐 {exc.text or exc}'
            except Exception as exc:
                self.tip = f'⚠️ {exc}'
            else:
                if self.config_entry:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=defaults
                    )
                    return self.async_create_entry(title='', data={})
                domain = f'{base}//'.split('/')[2]
                return self.async_create_entry(
                    title=OPEN_APIS.get(base) or domain,
                    data=user_input,
                )

        elif not service:
            schema = {
                vol.Optional(CONF_SERVICE, default=ZHI_PU_API): vol.In(OPEN_APIS),
            }

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), defaults),
            errors=errors,
            description_placeholders={"tip": self.tip},
        )


class ConfigFlow(config_entries.ConfigFlow, BasicFlow, domain=DOMAIN):
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry):
        """Return subentries supported by this handler."""
        return {
            "conversation": ConversationFlowHandler,
            "tts": TtsFlowHandler,
        }

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return OptionsFlow(entry)

    async def async_step_user(self, user_input=None):
        return await self.async_step_init(user_input, step_id="user")


class OptionsFlow(config_entries.OptionsFlow, BasicFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry):
        pass


class ConversationFlowHandler(config_entries.ConfigSubentryFlow, HasAttrs):
    """Handle subentry flow."""

    async def async_step_user(self, user_input=None):
        """Add a subentry."""
        entry = self._get_entry()
        base = entry.data.get(CONF_BASE, "")
        added_models = [
            sub.data[CONF_MODEL]
            for sub in entry.subentries.values()
        ]
        models = SERVICES.get(base, {}).get("models", [])
        try:
            api_models = await get_models(self.hass, dict(entry.data))
            for item in api_models:
                m = item.get("id")
                if m and m not in models:
                    models.append(m)
        except Exception:
            pass
        model = ""
        for m in models:
            if m not in added_models:
                model = m
                break
        defaults = {
            CONF_MODEL: model,
            CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
        }
        return await self.async_step_init(user_input, defaults)

    async def async_step_reconfigure(self, user_input=None):
        """Handle reconfiguration of a subentry."""
        defaults = self._get_reconfigure_subentry().data.copy()
        return await self.async_step_init(user_input, defaults)

    async def async_step_init(self, user_input=None, defaults=None):
        """User flow to create a subentry."""
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)
            name = user_input.get(CONF_NAME) or 'Agent'
            model = user_input[CONF_MODEL]
            if self.source == "user":
                return self.async_create_entry(
                    title=f"{name} ({model})".strip(), data=user_input
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=user_input,
            )

        hass_apis: list[SelectOptionDict] = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]
        schema = {
            vol.Required(CONF_MODEL): str,
            vol.Optional(CONF_NAME, default=""): str,
            vol.Optional(CONF_PROMPT, default=""): TemplateSelector(),
            vol.Optional(CONF_LLM_HASS_API, default=[]):
                SelectSelector(SelectSelectorConfig(options=hass_apis, multiple=True)),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), defaults),
            errors=errors,
            description_placeholders={"tip": self.tip},
        )


class TtsFlowHandler(config_entries.ConfigSubentryFlow, HasAttrs):
    """Handle subentry flow."""

    async def async_step_user(self, user_input=None):
        """Add a subentry."""
        defaults = {CONF_MODEL: ""}
        return await self.async_step_init(user_input, defaults)

    async def async_step_reconfigure(self, user_input=None):
        """Handle reconfiguration of a subentry."""
        defaults = self._get_reconfigure_subentry().data.copy()
        return await self.async_step_init(user_input, defaults)

    async def async_step_init(self, user_input=None, defaults=None):
        """User flow to create a subentry."""
        errors = {}
        if user_input is not None:
            model = user_input[CONF_MODEL]
            if self.source == "user":
                return self.async_create_entry(
                    title=f"TTS ({model})".strip(), data=user_input
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=user_input,
            )

        schema = {
            vol.Required(CONF_MODEL): str,
            vol.Optional("extra_body"): ObjectSelector(),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), defaults),
            errors=errors,
            description_placeholders={"tip": self.tip},
        )
