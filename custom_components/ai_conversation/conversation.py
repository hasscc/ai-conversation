import json

from homeassistant.components.conversation import (
    DOMAIN as ENTITY_DOMAIN,
    ConversationEntity as BaseEntity,
    ConversationInput,
    ConversationResult,
    ChatLog,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.network import get_url
from homeassistant.components.open_router.entity import OpenRouterEntity
from homeassistant.components import media_source
from homeassistant.components.media_player.browse_media import async_process_play_media_url

from . import HassEntry, BasicEntity
from .const import *
from .schemas import *

MAX_TOOL_ITERATIONS = 10

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Set up conversation entities."""
    for subentry_id, subentry in config_entry.subentries.items():
        if subentry.subentry_type != "conversation":
            continue
        entry = await HassEntry.async_init(hass, config_entry)
        async_add_entities(
            [ConversationEntity(entry, subentry)],
            config_subentry_id=subentry_id,
        )

class ConversationEntity(BasicEntity, BaseEntity, OpenRouterEntity):
    """Represent a conversation entity."""
    domain = ENTITY_DOMAIN

    def on_init(self):
        self.model = self.subentry.data.get(CONF_MODEL, '')
        self._attr_name = "Agent"
        self.entity_id = f'{self.domain}.agent_{self.model}'
        self._attr_unique_id = self.subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self.subentry.subentry_id)},
            name=self.subentry.title,
            model=self.model,
            manufacturer="SomeAI",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self):
        """Return a list of supported languages."""
        return MATCH_ALL

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Call the API."""
        options = self.subentry.data
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        await self._async_handle_chat_log(chat_log)
        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        structure_name: str | None = None,
        structure: vol.Schema | None = None,
    ) -> None:
        data = ChatCompletions(
            model=self.model,
            user=chat_log.conversation_id,
        )

        for content in chat_log.content:
            if msg := ChatMessage.from_conversation_content(content):
                data.messages.append(msg)

        if chat_log.llm_api:
            for tool in chat_log.llm_api.tools:
                func = ChatTool.from_hass_llm_tool(tool, chat_log.llm_api.custom_serializer)
                data.tools.append(func)

        if structure and structure_name:
            schema = ResponseJsonSchema(structure_name, structure, chat_log.llm_api)
            data.response_format = schema
            if ".bigmodel.cn..." in self.entry.get_config(CONF_BASE):
                data.response_format = Dict(type="json_object")
                data.messages.append(ChatMessage(
                    role="system",
                    content=(
                        "Please ensure that the response is in JSON schema:"
                        f"{json.dumps(schema.schema)}"
                    ),
                ))

        for _iteration in range(MAX_TOOL_ITERATIONS):
            result = await self.async_chat_completions(**data)
            data.messages.extend(
                [
                    msg
                    async for content in chat_log.async_add_delta_content_stream(
                        self.entity_id, result.message.to_conversation_content_delta()
                    )
                    if (msg := ChatMessage.from_conversation_content(content))
                ]
            )
            if not chat_log.unresponded_tool_results:
                break

    async def async_chat_completions(self, messages, **kwargs):
        model = kwargs.pop("model", None) or self.model
        data = ChatCompletions(model=model, messages=messages, **kwargs)
        try:
            result = await self.entry.async_chat_completions(data)
        except Exception as err:
            LOGGER.exception('chat_completions error: %s', data, exc_info=True)
            raise HomeAssistantError(f"Error talking to API: {err}") from err
        LOGGER.debug('chat_completions req: %s', data)
        if result.error:
            raise HomeAssistantError(f"Error talking to API: {result.error}")
        LOGGER.debug('chat_completions rsp: %s', result)
        return result

    async def async_explain_media(self, prompt='', image=None, video=None, tags=None, **kwargs):
        url = video or image
        if not url:
            return {'error': 'no url'}
        if media_source.is_media_source_id(url):
            media = await media_source.async_resolve_media(self.hass, url, None)
            url = media.url
        if not url.startswith('http'):
            url = async_process_play_media_url(self.hass, url)
        if not url.startswith('http') and video:
            return {'error': f'url error: {url}'}
        internal = get_url(self.hass, prefer_external=False)
        external = get_url(self.hass, prefer_external=True)
        url = url.replace(internal, external)
        if not prompt:
            prompt = 'Analyze and summarize'
        if tags:
            prompt += '''
            Please ensure that the response is in JSON schema:
            {
              "message": "string(Summary content, language: $lang)",
              "tags": ["Only return the matched tags ($tags)"]
            }
            '''.strip()
            tags = '|'.join(tags) if isinstance(tags, list) else str(tags)
            prompt = prompt.replace('$tags', tags)
            prompt = prompt.replace('$lang', self.hass.config.language or 'en')
        content = [{'type': 'text', 'text': prompt}]
        if video:
            content.append({'type': 'video_url', 'video_url': {'url': url}})
        else:
            content.append({'type': 'image_url', 'image_url': {'url': url}})
        if not (system_prompt := self.subentry.data.get(CONF_PROMPT)):
            system_prompt = f'Reply in the specified language ({self.hass.config.language}).'
        result = await self.async_chat_completions([
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': content},
        ])
        res = {'url': url}
        tags = res.setdefault('tags', [])
        message = result.message
        msg = message.content if message else ''
        if '```' in msg:
            arr = msg.split('```json')
            try:
                jss = str(arr[1].split('```')[0] if len(arr) > 1 else arr[0])
                dat = json.loads(jss.strip() or '{}')
                msg = dat.get('message', '')
                tags.extend(dat.get('tags', []))
                res['tags_string'] = ' '.join(map(lambda x: f'#{x}', tags))
            except Exception as exc:
                res['error'] = str(exc)
                res['result'] = result.to_dict()
        res['message'] = msg
        if message and 'reasoning_content' in message:
            res['reasoning'] = message.reasoning_content
        res['usage'] = result.usage
        return res
