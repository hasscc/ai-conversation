from homeassistant.components.conversation import (
    DOMAIN as ENTITY_DOMAIN,
    ConversationEntity as BaseEntity,
    ConversationInput,
    ConversationResult,
    ChatLog,
)
from homeassistant.helpers.network import get_url
from homeassistant.components import media_source
from homeassistant.components.media_player.browse_media import async_process_play_media_url

from . import HassEntry, BasicEntity
from .const import *
from .schemas import *

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

class ConversationEntity(BasicEntity, BaseEntity):
    """Represent a conversation entity."""
    domain = ENTITY_DOMAIN

    def on_init(self):
        self._attr_unique_id = self.subentry.subentry_id

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
