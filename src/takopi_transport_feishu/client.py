from __future__ import annotations

import json
import threading
from queue import Empty, Queue
from typing import Any

import anyio
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageRequest,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.client import Client
from lark_oapi.core.enum import LogLevel
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws import Client as WsClient

from takopi.logging import get_logger

from .messages import parse_incoming_message
from .render import text_message_content
from .settings import FeishuTransportSettings

logger = get_logger(__name__)

__all__ = ["FeishuClient", "BotIdentity"]


class BotIdentity:
    __slots__ = ("open_id", "name")

    def __init__(self, *, open_id: str | None, name: str | None) -> None:
        self.open_id = open_id
        self.name = name


def _parse_log_level(value: str) -> LogLevel:
    normalized = value.strip().upper()
    for level in LogLevel:
        if level.name == normalized:
            return level
    return LogLevel.INFO


class FeishuClient:
    def __init__(self, settings: FeishuTransportSettings) -> None:
        self._settings = settings
        self._client = (
            Client.builder()
            .app_id(settings.app_id)
            .app_secret(settings.app_secret)
            .domain(settings.domain)
            .log_level(_parse_log_level(settings.log_level))
            .build()
        )
        self._incoming: Queue[P2ImMessageReceiveV1] = Queue()
        self._ws_thread: threading.Thread | None = None
        self._bot_identity: BotIdentity | None = None

    @property
    def api_client(self) -> Client:
        return self._client

    @property
    def bot_identity(self) -> BotIdentity | None:
        return self._bot_identity

    def _on_message_event(self, data: P2ImMessageReceiveV1) -> None:
        logger.info("feishu.event.received", event_type="im.message.receive_v1")
        self._incoming.put(data)

    def _on_message_read(self, data: object) -> None:
        # Read receipts are pushed automatically; ignore to avoid SDK errors.
        del data

    def _build_event_handler(self) -> EventDispatcherHandler:
        builder = EventDispatcherHandler.builder(
            "",
            "",
            _parse_log_level(self._settings.log_level),
        )
        builder = builder.register_p2_im_message_receive_v1(self._on_message_event)
        for register_name, handler in (
            ("register_p2_im_message_message_read_v1", self._on_message_read),
            (
                "register_p2_im_chat_access_event_bot_p2p_chat_entered_v1",
                self._on_p2p_chat_entered,
            ),
            ("register_p2_im_chat_member_bot_added_v1", self._on_bot_added),
        ):
            try:
                builder = getattr(builder, register_name)(handler)
            except Exception:  # noqa: BLE001
                logger.debug("feishu.event.register_skipped", event=register_name)
        handler = builder.build()
        original = handler._do_without_validation

        def logged(payload: bytes) -> None:
            preview = payload.decode("utf-8", errors="replace")
            if len(preview) > 500:
                preview = preview[:500] + "..."
            logger.info("feishu.raw_event", payload=preview)
            return original(payload)

        handler._do_without_validation = logged  # type: ignore[method-assign]
        return handler

    def _on_p2p_chat_entered(self, data: object) -> None:
        event = getattr(data, "event", None)
        chat_id = getattr(event, "chat_id", None) if event is not None else None
        logger.info("feishu.event.p2p_chat_entered", chat_id=chat_id)

    def _on_bot_added(self, data: object) -> None:
        event = getattr(data, "event", None)
        chat_id = getattr(event, "chat_id", None) if event is not None else None
        logger.info("feishu.event.bot_added", chat_id=chat_id)

    def start_ws(self) -> None:
        if self._ws_thread is not None and self._ws_thread.is_alive():
            return

        handler = self._build_event_handler()
        ws_client = WsClient(
            self._settings.app_id,
            self._settings.app_secret,
            log_level=_parse_log_level(self._settings.log_level),
            event_handler=handler,
            domain=self._settings.domain,
        )

        def run_ws() -> None:
            logger.info("feishu.ws.starting")
            try:
                ws_client.start()
            except Exception:
                logger.exception("feishu.ws.failed")

        self._ws_thread = threading.Thread(
            target=run_ws,
            name="takopi-feishu-ws",
            daemon=True,
        )
        self._ws_thread.start()
        logger.info("feishu.ws.thread_started")

    async def close(self) -> None:
        # WS client blocks until process exit; daemon thread is enough for MVP.
        return None

    async def fetch_bot_identity(self) -> BotIdentity:
        if self._bot_identity is not None:
            return self._bot_identity

        def _fetch() -> BotIdentity:
            from lark_oapi.core.enum import AccessTokenType, HttpMethod
            from lark_oapi.core.http import Transport
            from lark_oapi.core.model import BaseRequest, RequestOption
            from lark_oapi.core.token.auth import verify

            req = BaseRequest()
            req.http_method = HttpMethod.GET
            req.uri = "/open-apis/bot/v3/info"
            req.token_types = {AccessTokenType.TENANT}
            option = RequestOption()
            verify(self._client._config, req, option)
            resp = Transport.execute(self._client._config, req, option)
            payload = json.loads(resp.content.decode("utf-8"))
            if payload.get("code") not in (0, None):
                logger.warning(
                    "feishu.bot_info.failed",
                    code=payload.get("code"),
                    msg=payload.get("msg"),
                )
                return BotIdentity(open_id=None, name=None)
            data = payload.get("data") or payload
            bot = data.get("bot") if isinstance(data, dict) else None
            if not isinstance(bot, dict):
                bot = data if isinstance(data, dict) else {}
            return BotIdentity(
                open_id=bot.get("open_id") or bot.get("openid"),
                name=bot.get("app_name") or bot.get("name"),
            )

        identity = await anyio.to_thread.run_sync(_fetch)
        self._bot_identity = identity
        return identity

    async def poll_incoming(
        self,
        *,
        timeout_s: float = 1.0,
        bot_open_id: str | None = None,
    ) -> list[Any]:
        def _drain() -> list[Any]:
            items: list[Any] = []
            deadline = timeout_s
            while True:
                try:
                    items.append(
                        self._incoming.get(timeout=deadline if not items else 0.05)
                    )
                except Empty:
                    break
                deadline = 0.05
            return items

        raw_events = await anyio.to_thread.run_sync(_drain)
        parsed = []
        for event in raw_events:
            message = parse_incoming_message(event, bot_open_id=bot_open_id)
            if message is not None:
                parsed.append(message)
            else:
                logger.info("feishu.event.ignored", reason="parse_filtered")
        return parsed

    async def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
        reply_in_thread: bool = False,
    ) -> str | None:
        content = text_message_content(text)

        def _send() -> str | None:
            if reply_to_message_id:
                req = (
                    ReplyMessageRequest.builder()
                    .message_id(reply_to_message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("text")
                        .content(content)
                        .reply_in_thread(reply_in_thread)
                        .build()
                    )
                    .build()
                )
                resp = self._client.im.v1.message.reply(req)
            else:
                req = (
                    CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("text")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                resp = self._client.im.v1.message.create(req)
            if not resp.success():
                logger.warning(
                    "feishu.send.failed",
                    code=resp.code,
                    msg=resp.msg,
                    chat_id=chat_id,
                )
                return None
            data = resp.data
            if data is None:
                return None
            return data.message_id

        return await anyio.to_thread.run_sync(_send)

    async def edit_text(self, *, message_id: str, text: str) -> bool:
        content = text_message_content(text)

        def _edit() -> bool:
            from lark_oapi.api.im.v1 import (
                UpdateMessageRequest,
                UpdateMessageRequestBody,
            )

            req = (
                UpdateMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    UpdateMessageRequestBody.builder()
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.update(req)
            if not resp.success():
                logger.warning(
                    "feishu.edit.failed",
                    code=resp.code,
                    msg=resp.msg,
                    message_id=message_id,
                )
                return False
            return True

        return await anyio.to_thread.run_sync(_edit)

    async def delete_message(self, *, message_id: str) -> bool:
        def _delete() -> bool:
            req = DeleteMessageRequest.builder().message_id(message_id).build()
            resp = self._client.im.v1.message.delete(req)
            return bool(resp.success())

        return await anyio.to_thread.run_sync(_delete)

    async def get_message_content(self, *, message_id: str) -> dict[str, object] | None:
        def _get() -> dict[str, object] | None:
            from lark_oapi.api.im.v1 import GetMessageRequest

            req = GetMessageRequest.builder().message_id(message_id).build()
            resp = self._client.im.v1.message.get(req)
            if not resp.success() or resp.data is None:
                return None
            items = getattr(resp.data, "items", None) or []
            if not items:
                return None
            item = items[0]
            body = getattr(item, "body", None)
            content = getattr(body, "content", None) if body is not None else None
            msg_type = getattr(item, "msg_type", None) or getattr(
                body, "msg_type", None
            )
            if not isinstance(content, str):
                return None
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                return {"text": content}
            if isinstance(payload, dict):
                payload.setdefault("_msg_type", msg_type)
                return payload
            return {"text": str(payload), "_msg_type": msg_type}

        return await anyio.to_thread.run_sync(_get)

    async def download_message_file(self, message_id: str) -> tuple[str, bytes] | None:
        payload = await self.get_message_content(message_id=message_id)
        if payload is None:
            return None
        msg_type = str(payload.get("_msg_type") or "")
        file_key = None
        filename = "upload.bin"
        if msg_type == "file":
            file_key = payload.get("file_key")
            filename = str(payload.get("file_name") or filename)
        elif msg_type == "image":
            file_key = payload.get("image_key")
            filename = "image.png"
        if not isinstance(file_key, str) or not file_key:
            return None

        def _download() -> bytes | None:
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            req = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type("file" if msg_type == "file" else "image")
                .build()
            )
            resp = self._client.im.v1.message_resource.get(req)
            if not resp.success():
                return None
            data = getattr(resp, "file", None) or getattr(resp, "data", None)
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            if hasattr(resp, "raw") and isinstance(resp.raw, (bytes, bytearray)):
                return bytes(resp.raw)
            return None

        content = await anyio.to_thread.run_sync(_download)
        if content is None:
            return None
        return filename, content

    async def send_file(
        self,
        *,
        chat_id: str,
        filename: str,
        payload: bytes,
        reply_to_message_id: str | None = None,
        reply_in_thread: bool = False,
    ) -> str | None:
        def _send() -> str | None:
            from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

            create_req = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(filename)
                    .file(payload)
                    .build()
                )
                .build()
            )
            create_resp = self._client.im.v1.file.create(create_req)
            if not create_resp.success() or create_resp.data is None:
                logger.warning(
                    "feishu.file_upload.failed",
                    code=create_resp.code,
                    msg=create_resp.msg,
                )
                return None
            file_key = create_resp.data.file_key
            if not file_key:
                return None
            content = json.dumps({"file_key": file_key, "file_name": filename})
            if reply_to_message_id:
                req = (
                    ReplyMessageRequest.builder()
                    .message_id(reply_to_message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("file")
                        .content(content)
                        .reply_in_thread(reply_in_thread)
                        .build()
                    )
                    .build()
                )
                resp = self._client.im.v1.message.reply(req)
            else:
                req = (
                    CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("file")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                resp = self._client.im.v1.message.create(req)
            if not resp.success():
                logger.warning(
                    "feishu.send_file.failed",
                    code=resp.code,
                    msg=resp.msg,
                    chat_id=chat_id,
                )
                return None
            data = resp.data
            return data.message_id if data is not None else None

        return await anyio.to_thread.run_sync(_send)
