# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic

# PY-TGCALLS 1.2.9 (ses odaklı) ile uyumlu, stabil ve Heroku dostu sürüm.

import asyncio
from ntgcalls import ConnectionNotFound, TelegramServerError
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, types, exceptions
from pytgcalls.pytgcalls_session import PyTgCallsSession

from anony import app, config, db, lang, logger, queue, userbot, yt
from anony.helpers import Media, Track, buttons, thumb


class TgCall(PyTgCalls):
    def __init__(self):
        # pyrogram.Client instance'larından açılan PyTgCalls client listesi
        self.clients: list[PyTgCalls] = []

    # -------------------------
    # BASİC KONTROLLER
    # -------------------------
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
        # Sesli sohbetten çıkmayı dene
        try:
            await client.leave_call(chat_id, False)
        except Exception:
            pass
        # Kuyruğu ve DB kayıtlarını temizle
        try:
            queue.clear(chat_id)
            await db.remove_call(chat_id)
        except Exception:
            pass

    # -------------------------
    # MEDYA ÇALMA
    # -------------------------
    async def play_media(
        self,
        chat_id: int,
        message: Message,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        """
        Sadece ses akışı (StreamAudio + HighQualityAudio).
        Otomatik reconnect için join_call denemeleri mevcut.
        """
        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)

        # Kapak
        _thumb = (
            await thumb.generate(media)
            if isinstance(media, Track)
            else config.DEFAULT_THUMB
        )

        # Kaynak dosya yoksa hata ver
        if not getattr(media, "file_path", None):
            return await message.edit_text(
                _lang["error_no_file"].format(config.SUPPORT_CHAT)
            )

     stream = types.AudioPiped(
    media.file_path,
    audio_parameters=types.HighQualityAudio()
     )

        # Otomatik reconnect / retry
        retry = 0
        while True:
            try:
                await client.join_call(chat_id, stream)
                media.playing = True
                await db.add_call(chat_id)

                # Şarkı bilgisini gönder/güncelle
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
                break  # başarı
            except exceptions.NoActiveGroupCall:
                # Sesli sohbet kapalı ise temiz çık
                await self.stop(chat_id)
                await message.edit_text(_lang["error_no_call"])
                return
            except (ConnectionNotFound, TelegramServerError) as e:
                retry += 1
                if retry <= 3:
                    # Küçük bekleme ile yeniden dene
                    backoff = min(1.5 * retry, 4.0)
                    logger.warning(f"[calls] join_call retry {retry}/3 (sleep {backoff:.1f}s) reason={type(e).__name__}")
                    await asyncio.sleep(backoff)
                    continue
                # 3 deneme sonrası vazgeç
                await self.stop(chat_id)
                await message.edit_text(_lang["error_tg_server"])
                return
            except Exception as e:
                # Bilinmeyen hata: düşürmeden temizle
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

        # Mevcut mesajı temizle
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

        # Dosya indirme (yoksa)
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
    # EVENT DEKORATÖRLERİ (uyumluysa)
    # -------------------------
    async def _wire_events(self, client: PyTgCalls) -> None:
        """
        py-tgcalls 1.2.9 için güvenli event bağlama.
        Bazı ortamlarda decorator isimleri/objeleri değişebildiği için
        getattr ile korumalı bağlama yapıyoruz.
        """

        # Stream bittiğinde sıradakine geç (sadece ses)
        on_stream_end = getattr(client, "on_stream_end", None)
        if callable(on_stream_end):
            try:
                @on_stream_end()
                async def _on_stream_end(_, update):
                    # update tip güvenliği: StreamAudioEnded varsa problem yok; yoksa chat_id almayı deneriz
                    chat_id = getattr(update, "chat_id", None)
                    if chat_id is None:
                        # Bazı varyantlarda update.chat_id olmayabilir; bu durumda logla ve çık
                        logger.warning("[calls] on_stream_end: chat_id missing on update")
                        return
                    try:
                        await self.play_next(chat_id)
                    except Exception as e:
                        logger.error(f"[calls] on_stream_end handler error: {e}")
            except Exception as e:
                logger.warning(f"[calls] on_stream_end bind failed: {e}")
        else:
            logger.info("[calls] on_stream_end decorator not available; auto-next will depend on commands")

        # Chat kapandı / kick vb. gibi eventler bazı sürümlerde yok.
        # Uyumsuzluk yaşamamak için burada bilinçli olarak ek event bağlamıyoruz.

    # -------------------------
    # BAŞLATMA
    # -------------------------
    async def boot(self) -> None:
        # py-tgcalls banner uyarısını gizle
        PyTgCallsSession.notice_displayed = True

        # Tüm userbot client'ları ile PyTgCalls başlat
        for ub in userbot.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            await self._wire_events(client)

        logger.info("PyTgCalls client(s) started.")
