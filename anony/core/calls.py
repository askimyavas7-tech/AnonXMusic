# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic

# PY-TGCALLS SES SÜRÜMÜ ile uyumlu (1.2.9)
# STABİL ÇALIŞAN MÜZİK SİSTEMİ

from ntgcalls import ConnectionNotFound, TelegramServerError
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, types, exceptions
from pytgcalls.pytgcalls_session import PyTgCallsSession

from anony import app, config, db, lang, logger, queue, userbot, yt
from anony.helpers import Media, Track, buttons, thumb


class TgCall(PyTgCalls):
    def __init__(self):
        self.clients = []

    async def pause(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=True)
        return await client.pause_stream(chat_id)

    async def resume(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=False)
        return await client.resume_stream(chat_id)

    async def stop(self, chat_id: int) -> None:
        client = await db.get_assistant(chat_id)
        try:
            await client.leave_call(chat_id, False)
        except:
            pass
        try:
            queue.clear(chat_id)
            await db.remove_call(chat_id)
        except:
            pass
            async def play_media(
        self,
        chat_id: int,
        message: Message,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)
        _thumb = (
            await thumb.generate(media)
            if isinstance(media, Track)
            else config.DEFAULT_THUMB
        )

        if not media.file_path:
            return await message.edit_text(
                _lang["error_no_file"].format(config.SUPPORT_CHAT)
            )

        stream = types.StreamAudio(
            media.file_path,
            types.HighQualityAudio(),
        )

        try:
            await client.join_call(chat_id, stream)
            media.playing = True
            await db.add_call(chat_id)
            await message.edit_media(
                InputMediaPhoto(
                    media=_thumb,
                    caption=_lang["play_media"].format(
                        media.url,
                        media.title,
                        media.duration,
                        media.user,
                    ),
                ),
                reply_markup=buttons.controls(chat_id),
            )
        except exceptions.NoActiveGroupCall:
            await self.stop(chat_id)
            await message.edit_text(_lang["error_no_call"])
        except (ConnectionNotFound, TelegramServerError):
            await self.stop(chat_id)
            await message.edit_text(_lang["error_tg_server"])
            async def play_next(self, chat_id: int) -> None:
        if not await db.get_call(chat_id):
            return

        current = queue.get_current(chat_id)
        if current:
            try:
                await app.delete_messages(
                    chat_id=chat_id,
                    message_ids=[current.message_id],
                    revoke=True,
                )
            except:
                pass

        media = queue.get_next(chat_id)
        if not media:
            return await self.stop(chat_id)

        _lang = await lang.get_lang(chat_id)
        msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])

        if not media.file_path:
            try:
                media.file_path = await yt.download(media.id, video=media.video)
            except:
                await self.stop(chat_id)
                return await msg.edit_text(
                    _lang["error_no_file"].format(config.SUPPORT_CHAT)
                )

        media.message_id = msg.id
        await self.play_media(chat_id, msg, media)
        async def ping(self) -> float:
        if not self.clients:
            return 0.0
        pings = [client.ping for client in self.clients if hasattr(client, "ping")]
        return round(sum(pings) / len(pings), 2) if pings else 0.0

    async def boot(self) -> None:
        from pytgcalls import PyTgCalls
        PyTgCallsSession.notice_displayed = True

        for ub in userbot.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            await self.decorators(client)

        logger.info("PyTgCalls client(s) started.")
