# anony/core/calls.py
# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic

# PY-TGCALLS 1.2.9 (ses odaklı) uyumlu, Heroku-stabil sürüm.

import asyncio
from typing import List, Optional

from ntgcalls import ConnectionNotFound, TelegramServerError
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, exceptions
from pytgcalls.pytgcalls_session import PyTgCallsSession

from anony import app, config, db, lang, logger, queue, userbot, yt
from anony.helpers import Media, Track, buttons, thumb


def _resolve_input_stream_classes():
    """
    Bazı py-tgcalls 1.2.9 paketlerinde sınıf yolları farklı olabiliyor.
    Güvenli şekilde AudioPiped ve HighQualityAudio sınıflarını bulup döndürür.
    Dönüş: (AudioPiped, HighQualityAudio) veya (None, None)
    """
    try:
        # En yaygın düzen
        from pytgcalls.types.input_stream import AudioPiped  # type: ignore
        from pytgcalls.types.input_stream.quality import HighQualityAudio  # type: ignore
        return AudioPiped, HighQualityAudio
    except Exception:
        try:
            # Alternatif bazı build'ler
            from pytgcalls.types import AudioPiped  # type: ignore
            from pytgcalls.types import HighQualityAudio  # type: ignore
            return AudioPiped, HighQualityAudio
        except Exception as e:
            logger.error(f"[calls] AudioPiped/HighQualityAudio import edilemedi: {e}")
            return None, None


class TgCall:
    """
    PyTgCalls client’larını yöneten ve ses akışını kontrol eden sarmalayıcı.
    """
    def __init__(self):
        self.clients: List[PyTgCalls] = []

    # -------------------------
    # TEMEL KONTROLLER
    # -------------------------
    async def pause(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=True)
        # 1.2.9’da metod isimleri pause_stream/resume_stream
        return await client.pause_stream(chat_id)

    async def resume(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=False)
        return await client.resume_stream(chat_id)

    async def stop(self, chat_id: int) -> None:
        client = await db.get_assistant(chat_id)
        # Sesten çık
        try:
            await client.leave_call(chat_id, False)
        except Exception:
            pass
        # Kuyruk + DB temizliği
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
        seek_time: int = 0,  # Not: 1.2.9’da seek’i doğrudan desteklemiyoruz
    ) -> None:
        """
        Sadece ses akışı: AudioPiped + HighQualityAudio
        """
        _lang = await lang.get_lang(chat_id)
        client = await db.get_assistant(chat_id)

        AudioPiped, HighQualityAudio = _resolve_input_stream_classes()
        if AudioPiped is None or HighQualityAudio is None:
            await self.stop(chat_id)
            return await message.edit_text(_lang["error_tg_server"])

        # Kapak
        _thumb = (
            await thumb.generate(media)
            if isinstance(media, Track)
            else config.DEFAULT_THUMB
        )

        # Kaynak dosya
        if not getattr(media, "file_path", None):
            return await message.edit_text(
                _lang["error_no_file"].format(config.SUPPORT_CHAT)
            )

        # Akış oluştur
        stream = AudioPiped(
            media.file_path,
            audio_parameters=HighQualityAudio(),
        )

        # Bağlan / yeniden dene
        retry = 0
        while True:
            try:
                await client.join_call(chat_id, stream)
                media.playing = True
                await db.add_call(chat_id)

                # Şarkı bilgisini gönder
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
                return
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
    # SIRADAKİ PARÇA
    # -------------------------
    async def play_next(self, chat_id: int) -> None:
        if not await db.get_call(chat_id):
            return

        # Eski mesajı sil
        current = queue.get_current(chat_id)
        if current and getattr(current, "message_id", None):
            try:
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

        # Gerekirse indir
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
        vals = []
        for c in self.clients:
            p = getattr(c, "ping", None)
            if isinstance(p, (int, float)):
                vals.append(p)
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    # -------------------------
    # EVENT BAĞLAMA (sürüm güvenli)
    # -------------------------
    async def _wire_events(self, client: PyTgCalls) -> None:
        """
        py-tgcalls 1.2.9’da event API’si paketlere göre değişebiliyor.
        Uyum için dekoratörleri 'getattr' ile bağlarız.
        """

        # Akış bitti → sıradakine geç
        on_stream_end = getattr(client, "on_stream_end", None)
        if callable(on_stream_end):
            try:
                @on_stream_end()
                async def _on_stream_end(_, update):
                    chat_id: Optional[int] = getattr(update, "chat_id", None)
                    if chat_id is None:
                        logger.warning("[calls] on_stream_end: chat_id missing")
                        return
                    try:
                        await self.play_next(chat_id)
                    except Exception as e:
                        logger.error(f"[calls] on_stream_end handler error: {e}")
            except Exception as e:
                logger.warning(f"[calls] bind on_stream_end failed: {e}")
        else:
            logger.info("[calls] on_stream_end decorator not available")

        # Not: 1.2.9’da ChatUpdate benzeri event isimleri değişken.
        # Uyuşmazlık yaşamamak için burada başka event bağlamıyoruz.

    # -------------------------
    # BAŞLAT
    # -------------------------
    async def boot(self) -> None:
        PyTgCallsSession.notice_displayed = True

        # Tüm userbot client’larını başlat
        for ub in userbot.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            await self._wire_events(client)

        logger.info("PyTgCalls client(s) started.")
