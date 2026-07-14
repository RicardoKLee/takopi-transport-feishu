from __future__ import annotations

from takopi.transport import MessageRef, RenderedMessage, SendOptions

from .client import FeishuClient

__all__ = ["FeishuTransport"]


class FeishuTransport:
    def __init__(self, client: FeishuClient) -> None:
        self._client = client

    async def close(self) -> None:
        await self._client.close()

    @staticmethod
    def _extract_followups(message: RenderedMessage) -> list[RenderedMessage]:
        followups = message.extra.get("followups")
        if not isinstance(followups, list):
            return []
        return [item for item in followups if isinstance(item, RenderedMessage)]

    async def _send_followups(
        self,
        *,
        chat_id: str,
        followups: list[RenderedMessage],
        reply_to_message_id: str | None,
    ) -> None:
        for followup in followups:
            await self._client.send_text(
                chat_id=chat_id,
                text=followup.text,
                reply_to_message_id=reply_to_message_id,
            )

    async def send(
        self,
        *,
        channel_id: str | int,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        chat_id = str(channel_id)
        reply_to = options.reply_to if options is not None else None
        reply_message_id = str(reply_to.message_id) if reply_to is not None else None
        thread_id = options.thread_id if options is not None else None
        reply_in_thread = thread_id is not None

        sent_id = await self._client.send_text(
            chat_id=chat_id,
            text=message.text,
            reply_to_message_id=reply_message_id,
            reply_in_thread=reply_in_thread,
        )
        if sent_id is None:
            return None

        followups = self._extract_followups(message)
        if followups:
            followup_reply = message.extra.get("followup_reply_to_message_id")
            if isinstance(followup_reply, (str, int)):
                reply_target = str(followup_reply)
            else:
                reply_target = sent_id
            await self._send_followups(
                chat_id=chat_id,
                followups=followups,
                reply_to_message_id=reply_target,
            )

        return MessageRef(
            channel_id=chat_id,
            message_id=sent_id,
            thread_id=str(thread_id) if thread_id is not None else None,
        )

    async def edit(
        self,
        *,
        ref: MessageRef,
        message: RenderedMessage,
        wait: bool = True,
    ) -> MessageRef | None:
        del wait
        ok = await self._client.edit_text(
            message_id=str(ref.message_id),
            text=message.text,
        )
        if not ok:
            return None
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        return await self._client.delete_message(message_id=str(ref.message_id))
