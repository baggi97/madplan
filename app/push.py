"""Web Push til familiens telefoner.

Websitet er stedet man vælger og handler — det her sender kun et praj om at
der er noget nyt, ligesom Telegram-beskeden. Er VAPID-nøglerne tomme, sker
der ingenting.

**Kræver HTTPS.** Service workers og Push API'et virker kun i sikker kontekst.
`http://<NAS-IP>:8099` er ikke nok; browseren nægter at registrere workeren.
Undtagelsen er `localhost`, som browsere regner for sikker.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

from pywebpush import WebPushException, webpush

from . import config, store

log = logging.getLogger(__name__)

# Push-nyttelast har en hård grænse omkring 4 kB efter kryptering
MAKS_TEKST = 300

# Hvor længe push-tjenesten skal gemme beskeden hvis telefonen er slukket.
# Standarden er 0 — altså "lever nu eller smid væk" — og så ville en telefon
# der lå slukket søndag morgen aldrig få beskeden. Tolv timer rækker fra
# forslagene hentes kl. 8 til deadline kl. 18.
LEVETID = 12 * 3600


def slaaet_til() -> bool:
    return bool(config.VAPID_OFFENTLIG_NOEGLE and config.VAPID_PRIVAT_NOEGLE)


def _krav() -> dict:
    """VAPID-claims. `sub` skal være en mailto: eller https: som push-tjenesten
    kan kontakte hvis vi opfører os dårligt."""
    return {"sub": config.VAPID_KONTAKT}


def _send_en(abonnement: dict, nyttelast: str) -> int | None:
    """Sender til én browser. Returnerer HTTP-koden ved fejl, ellers None."""
    try:
        webpush(
            subscription_info=abonnement,
            data=nyttelast,
            vapid_private_key=config.VAPID_PRIVAT_NOEGLE,
            vapid_claims=_krav(),
            ttl=LEVETID,
            timeout=20,
        )
        return None
    except WebPushException as e:
        kode = getattr(e.response, "status_code", None)
        log.warning("Push afvist (%s): %s", kode, str(e)[:200])
        return kode or 0


async def send(titel: str, tekst: str, sti: str = "/") -> None:
    """Sender til alle abonnementer og rydder de døde væk.

    404 og 410 betyder at browseren har smidt abonnementet væk — telefonen er
    nulstillet, appen fjernet fra hjemmeskærmen, eller brugeren har slået
    notifikationer fra. Så skal vi ikke blive ved med at prøve.
    """
    if not slaaet_til():
        return

    abonnementer = store.hent_abonnementer()
    if not abonnementer:
        return

    nyttelast = json.dumps(
        {"titel": titel, "tekst": tekst[:MAKS_TEKST], "sti": sti}, ensure_ascii=False
    )

    resultater = await asyncio.gather(
        *(asyncio.to_thread(_send_en, a, nyttelast) for a in abonnementer)
    )

    doede = [a for a, kode in zip(abonnementer, resultater) if kode in (404, 410)]
    if doede:
        endepunkter = {a["endpoint"] for a in doede}
        store.gem_abonnementer(
            [a for a in abonnementer if a["endpoint"] not in endepunkter]
        )
        log.info("Fjernede %d udløbne push-abonnementer", len(doede))

    sendt = sum(1 for k in resultater if k is None)
    log.info("Push sendt til %d af %d", sendt, len(abonnementer))


def _b64(raa: bytes) -> str:
    """base64url uden padding — det format både browseren og VAPID vil have."""
    return base64.urlsafe_b64encode(raa).rstrip(b"=").decode()


def _lav_noegler() -> tuple[str, str]:
    """Genererer et VAPID-nøglepar (offentlig, privat).

    Den offentlige nøgle er det ukomprimerede P-256-punkt på 65 bytes, som
    browseren vil have i `applicationServerKey`. Den private er de rå 32 bytes.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    privat = ec.generate_private_key(ec.SECP256R1())
    offentlig_raa = privat.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    privat_raa = privat.private_numbers().private_value.to_bytes(32, "big")
    return _b64(offentlig_raa), _b64(privat_raa)


if __name__ == "__main__":
    offentlig, privat = _lav_noegler()
    print("# Læg disse i .env — den private nøgle er en hemmelighed.")
    print("VAPID_OFFENTLIG_NOEGLE=" + offentlig)
    print("VAPID_PRIVAT_NOEGLE=" + privat)
    print("VAPID_KONTAKT=mailto:dig@eksempel.dk")
