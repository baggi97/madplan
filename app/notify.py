"""Valgfri Telegram-besked.

Websitet er stedet hvor man vælger og handler — det her sender kun et praj om
at der er noget nyt, med et link. Er TELEGRAM_TOKEN tom, sker der ingenting.
"""
from __future__ import annotations

import logging

import httpx

from . import config

log = logging.getLogger(__name__)


async def send(tekst: str) -> None:
    if not config.notifikationer_slaaet_til():
        return

    if config.BASE_URL:
        tekst = tekst + "\n\n" + config.BASE_URL

    url = "https://api.telegram.org/bot{}/sendMessage".format(config.TELEGRAM_TOKEN)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            svar = await client.post(
                url,
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "text": tekst,
                    "disable_web_page_preview": False,
                },
            )
        data = svar.json()
        if not data.get("ok"):
            log.error("Telegram afviste beskeden: %s", data.get("description"))
    except httpx.RequestError as e:
        log.warning("Kunne ikke sende Telegram-besked: %s", e)
