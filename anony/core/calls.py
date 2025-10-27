# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic
#
# Stabil ses akışı için: py-tgcalls==1.2.9, ntgcalls==1.2.3

import asyncio
from ntgcalls import ConnectionNotFound, TelegramServerError
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, exceptions
from pytgcalls.pytgcalls_session import PyTgCallsSession

# >>> py-tgcalls 1.2.9'da doğru import yolları
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio

from anony import app, config, db, lang, logger, queue, userbot, yt
from anony.helpers import Media, Track, buttons, thumb


class TgCall(PyTgCalls):
    def __init__(self):
        # pyrogram.Client tabanlı tüm PyTgCalls client'ları
        self.clients: list[PyTgCalls] = []

    # -------------------------
    # BASİC KONTROLLER
    # -------------------------
    async def pause(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=True)
        # v1.2.9'da pause_stream / resume_stream var
        return await client.pause_stream(chat_id)

    async def resume(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=False)
        return await client.resume_stream(chat_id)

    async def stop(self, chat_id: int) -> None:
        client = await db.get_assistant(chat_id)
        # Sesli sohbetten çık
        try:
            await client.leave_call(chat_id, False)
        except Exception:
            pass
        # Kuyruğu ve DB kaydını temizle
        try:
            queue.clear(chat_id)
            await db.remove_call(chat_id)
        except Exception:
            pass

    # -------------------------
    # MEDYA ÇALMA (SES)
    # -------------------------
    async def play_media(
        self,
        chat_id: int,
        message: Message,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        """
        Ses akışı: AudioPiped + HighQualityAudio.
        py-tgcalls 1.2.9 API’si ile uyumlu.
        """
        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)

        # Kapak görseli
        _thumb = (
            await thumb.generate(media)
            if isinstance(media, Track)
            else config.DEFAULT_THUMB
        )

        # Dosya yolu şart
        if not getattr(media, "file_path", None):
            return await message.edit_text(
                _lang["error_no_file"].format(config.SUPPORT_CHAT)
            )

        # ffmpeg -ss desteği: AudioPiped input'unda seek vermek için
        # input_options ile -ss gönderiyoruz (seek_time > 1 ise)
        input_opts = []
        if isinstance(seek_time, int) and seek_time > 1:
            input_opts = ["-ss", str(seek_time)]

        stream = AudioPiped(
            media.file_path,
            audio_parameters=HighQualityAudio(),
            input_stream_params={"input_options": input_opts} if input_opts else None,
        )

        # Otomatik reconnect / retry
        retry = 0
        while True:
            try:
                # v1.2.9'da join_call kullanımı
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
                break  # başarıyla bağlandı
            except exceptions.NoActiveGroupCall:
                await self.stop(chat_id)
                await message.edit_text(_lang["error_no_call"])
                return
            except (ConnectionNotFound, TelegramServerError) as e:
                retry += 1
                if retry <= 3:
                    backoff = min(1.5 * retry, 4.0)
                    logger.warning(
                        f"[calls] join_call retry {retry}/3 (sleep {backoff:.1f}s) reason={type(e).__name__}"
                    )
                    await asyncio.sleep(backoff)
                    continue
                await self.stop(chat_id)
                await message.edit_text(_lang["error_tg_server"])
                return
            except Exception as e:
                logger.error(f"[calls] join_call unexpected error: {e}")
                await self.stop(chat_id)
                await message.edit_text(_lang["error_tg_server"])
                return

    # -------------------------
    # SIRADAKİ PARÇAYI ÇAL
    # -------------------------
    async def play_next(self, chat_id: int) -> None:
        if not await db.get_call(chat_id):
            return

        # Mevcut şarkı mesajını temizle
        current = queue.get_current(chat_id)
        if current:
            try:
                if getattr(current, "message_id", None):
                    await app.delete_messages(
                        chat_id=chat_id,
                        message_ids=[current.message_id],
                        revoke=True,
                    )
            except Exception:
                pass

        # Kuyruktan sıradaki
        media = queue.get_next(chat_id)
        if not media:
            return await self.stop(chat_id)

        _lang = await lang.get_lang(chat_id)
        msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])

        # Dosya yoksa indir
        if not getattr(media, "file_path", None):
            try:
                media.file_path = await yt.download(media.id, video=media.video)
            except Exception:
                await self.stop(chat_id)
                return await msg.edit_text(
                    _lang["error_no_file"].format(config.SUPPORT_CHAT)
                )

        media.message_id = msg.id
        await self.play_media(chat_id, msg, media)

    # -------------------------
    # PİNG
    # -------------------------
    async def ping(self) -> float:
        if not self.clients:
            return 0.0
        pings = [getattr(client, "ping", 0.0) for client in self.clients]
        pings = [p for p in pings if isinstance(p, (int, float))]
        return round(sum(pings) / len(pings), 2) if pings else 0.0

    # -------------------------
    # EVENT BAĞLAMA (uyumluysa)
    # -------------------------
    async def _wire_events(self, client: PyTgCalls) -> None:
        """
        py-tgcalls 1.2.9 bazı ortamlarda farklı decorator isimleri barındırabiliyor.
        Güvenli (getattr) bağlama yapıyoruz.
        """
        on_stream_end = getattr(client, "on_stream_end", None)
        if callable(on_stream_end):
            try:
                @on_stream_end()
                async def _on_stream_end(_, update):
                    chat_id = getattr(update, "chat_id", None)
                    if chat_id is None:
                        logger.warning("[calls] on_stream_end: chat_id missing on update")
                        return
                    try:
                        await self.play_next(chat_id)
                    except Exception as e:
                        logger.error(f"[calls] on_stream_end handler error: {e}")
            except Exception as e:
                logger.warning(f"[calls] on_stream_end bind failed: {e}")
        else:
            logger.info("[calls] on_stream_end decorator not available; auto-next disabled")

    # -------------------------
    # BAŞLATMA
    # -------------------------
    async def boot(self) -> None:
        # py-tgcalls banner uyarısını kapat
        PyTgCallsSession.notice_displayed = True

        # Tüm userbot client’larıyla PyTgCalls başlat
        for ub in userbot.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            await self._wire_events(client)

        logger.info("PyTgCalls client(s) started.")
