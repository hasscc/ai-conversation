import json
import anyio

from aiohttp import web
from aiohttp_sse import sse_response
from aiohttp.web_exceptions import HTTPBadRequest, HTTPNotFound
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types
from mcp.server import Server
from collections.abc import Sequence

from homeassistant.components import conversation
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.util import uuid

from .const import *

_LOGGER = logging.getLogger(__name__)
MESSAGES_API = f"/{DOMAIN}/messages/{{session_id}}"


@callback
def async_register(hass: HomeAssistant):
    """Register the SSE API."""
    hass.http.register_view(ModelContextProtocolSSEView())
    hass.http.register_view(ModelContextProtocolMessagesView())


class ModelContextProtocolSSEView(HomeAssistantView):
    """Model Context Protocol SSE endpoint."""

    name = f"{DOMAIN}:sse"
    url = f"/{DOMAIN}/sse"
    cors_allowed = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        hass = request.app[KEY_HASS]
        sessions = hass.data[DOMAIN].setdefault("mcp_sessions", {})
        session_id = uuid.random_uuid_hex()

        agent_id = request.query.get("agent_id")
        if not agent_id:
            from . import HassEntry
            for entry in HassEntry.ALL.values():
                for entity in entry.entities.values():
                    if not isinstance(entity, conversation.ConversationEntity):
                        continue
                    agent_id = entity.entity_id
                    break
        elif "." not in agent_id:
            agent_id = f"{conversation.DOMAIN}.{agent_id}"

        read_stream: MemoryObjectReceiveStream[types.JSONRPCMessage | Exception]
        read_stream_writer: MemoryObjectSendStream[types.JSONRPCMessage | Exception]
        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

        write_stream: MemoryObjectSendStream[types.JSONRPCMessage]
        write_stream_reader: MemoryObjectReceiveStream[types.JSONRPCMessage]
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        sessions[session_id] = read_stream_writer

        async with sse_response(request) as response:
            server = await create_server(hass, agent_id)
            options = await hass.async_add_executor_job(server.create_initialization_options)
            session_uri = MESSAGES_API.format(session_id=session_id)
            _LOGGER.debug("Sending SSE endpoint: %s", session_uri)
            await response.send(session_uri, event="endpoint")

            async def sse_reader() -> None:
                """Forward MCP server responses to the client."""
                async for message in write_stream_reader:
                    _LOGGER.debug("Sending SSE message: %s", message)
                    await response.send(
                        message.model_dump_json(by_alias=True, exclude_none=True),
                        event="message",
                    )

            async with anyio.create_task_group() as tg:
                tg.start_soon(sse_reader)
                await server.run(read_stream, write_stream, options)
                return response


class ModelContextProtocolMessagesView(HomeAssistantView):
    """Model Context Protocol messages endpoint."""

    name = f"{DOMAIN}:messages"
    url = MESSAGES_API
    requires_auth = False
    cors_allowed = True

    async def post(
        self,
        request: web.Request,
        session_id: str,
    ) -> web.StreamResponse:
        hass = request.app[KEY_HASS]
        sessions = hass.data[DOMAIN].setdefault("mcp_sessions", {})
        if not session_id or session_id not in sessions:
            raise HTTPNotFound(text=f"Could not find session ID '{session_id}'")

        if (read_stream_writer := sessions.get(session_id)) is None:
            _LOGGER.info("Could not find session ID: '%s'", session_id)
            raise HTTPNotFound(text=f"Could not find session ID '{session_id}'")

        json_data = await request.json()
        try:
            message = types.JSONRPCMessage.model_validate(json_data)
        except ValueError as err:
            _LOGGER.info("Failed to parse message: %s", err)
            raise HTTPBadRequest(text="Could not parse message") from err

        _LOGGER.debug("Received client message: %s", message)
        await read_stream_writer.send(message)
        return web.Response(status=200)


async def create_server(hass: HomeAssistant, agent_id=None):
    server = Server(DOMAIN)

    @server.list_tools()  # type: ignore[no-untyped-call, misc]
    async def list_tools() -> list[types.Tool]:
        """List available time tools."""
        return [
            types.Tool(
                name="ha_conversation",
                description="Send conversation request to Home Assistant conversation agent",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The conversation text to send to Home Assistant",
                        },
                    },
                    "required": ["text"],
                },
            )
        ]

    @server.call_tool()  # type: ignore[no-untyped-call, misc]
    async def call_tool(name: str, arguments: dict) -> Sequence[types.TextContent]:
        """Handle calling tools."""
        result = None

        if name == "ha_conversation":
            text = arguments["text"]
            result = await hass.services.async_call(
                conversation.DOMAIN,
                conversation.SERVICE_PROCESS,
                {
                    "agent_id": agent_id,
                    "text": text,
                },
                blocking=True,
                return_response=True,
            )

        if result is None:
            raise ValueError(f"Unknown tool: {name}")
        return [
            types.TextContent(type="text", text=json.dumps(result)),
        ]

    return server
