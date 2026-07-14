from __future__ import annotations

import json

from lark_oapi.client import Client
from lark_oapi.core.enum import AccessTokenType, HttpMethod
from lark_oapi.core.http import Transport
from lark_oapi.core.json import JSON
from lark_oapi.core.model import BaseRequest, RequestOption
from lark_oapi.core.token.auth import verify
from lark_oapi.api.im.v1 import ListChatRequest

from takopi.logging import get_logger

from .settings import FeishuTransportSettings

logger = get_logger(__name__)

__all__ = ["run_startup_diagnostics"]


def run_startup_diagnostics(
    client: Client,
    settings: FeishuTransportSettings,
) -> None:
    callback_type = _fetch_callback_type(client, settings.app_id)
    chat_count = _count_bot_chats(client)
    logger.info(
        "feishu.diagnostics",
        callback_type=callback_type,
        chat_count=chat_count,
        domain=settings.domain,
    )
    if callback_type and callback_type != "websocket":
        logger.warning(
            "feishu.diagnostics.callback_mismatch",
            callback_type=callback_type,
            expected="websocket",
            hint="Set event subscription mode to long connection in Feishu Open Platform.",
        )
    if chat_count == 0:
        logger.warning(
            "feishu.diagnostics.no_chats",
            hint=(
                "Open the bot from Feishu Workbench and send a DM first, "
                "or add the bot to a group before messaging."
            ),
        )


def _fetch_callback_type(client: Client, app_id: str) -> str | None:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = f"/open-apis/application/v6/applications/{app_id}?lang=zh_cn"
    req.token_types = {AccessTokenType.TENANT}
    option = RequestOption()
    try:
        verify(client._config, req, option)
        resp = Transport.execute(client._config, req, option)
        body = json.loads(resp.content.decode("utf-8"))
        app = (body.get("data") or {}).get("app") or {}
        callback_info = app.get("callback_info") or {}
        value = callback_info.get("callback_type")
        return str(value) if value else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("feishu.diagnostics.app_info_failed", error=str(exc))
        return None


def _count_bot_chats(client: Client) -> int:
    try:
        req = ListChatRequest.builder().page_size(50).build()
        resp = client.im.v1.chat.list(req)
        if not resp.success() or resp.data is None:
            return 0
        payload = json.loads(JSON.marshal(resp.data))
        items = payload.get("items")
        return len(items) if isinstance(items, list) else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("feishu.diagnostics.list_chat_failed", error=str(exc))
        return 0
