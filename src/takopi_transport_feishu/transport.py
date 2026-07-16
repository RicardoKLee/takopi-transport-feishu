from __future__ import annotations

from takopi.transport import MessageRef, RenderedMessage, SendOptions

from .card import build_card, card_message_content
from .client import FeishuClient

__all__ = ["FeishuTransport"]


class FeishuTransport:
    def __init__(self, client: FeishuClient, *, use_card: bool = True) -> None:
        self._client = client
        self._use_card = use_card

    async def close(self) -> None:
        await self._client.close()

    def _build_card_content(
        self, text: str, *, streaming: bool = False, show_stop_button: bool = False
    ) -> str:
        return card_message_content(
            build_card(text, streaming=streaming, show_stop_button=show_stop_button)
        )

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
            await self._client.send_message(
                chat_id=chat_id,
                text=followup.text,
                use_card=self._use_card,
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

        text = message.text

        sent_id = await self._client.send_message(
            chat_id=chat_id,
            text=text,
            use_card=self._use_card,
            streaming=self._use_card,
            show_stop_button=self._use_card,
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
        if self._use_card:
            ok = await self._client.edit_card(
                message_id=str(ref.message_id),
                card_content=self._build_card_content(
                    message.text, streaming=False, show_stop_button=True
                ),
            )
        else:
            ok = await self._client.edit_text(
                message_id=str(ref.message_id),
                text=message.text,
            )
        if not ok:
            return None
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        return await self._client.delete_message(message_id=str(ref.message_id))
