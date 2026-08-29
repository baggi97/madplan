"""Ugens forløb.

hent_forslag()   → hent tilbud, foreslå retter (søndag morgen eller manuelt)
lav_madplan()    → opskrifter + indkøbsliste ud fra de valgte retter
deadline()       → kører lav_madplan hvis ingen har trykket selv
"""
from __future__ import annotations

import asyncio
import logging

from . import ai, config, notify, push, rema, store

log = logging.getLogger(__name__)

# Sikrer at to samtidige klik ikke starter det samme AI-kald to gange
_laas = asyncio.Lock()


async def hent_forslag(gennemtving: bool = False) -> None:
    async with _laas:
        noegle = store.uge_noegle()
        uge = store.hent_uge(noegle)

        if uge["status"] == store.ARBEJDER:
            return
        if uge["forslag"] and not gennemtving:
            log.info("Forslag findes allerede for %s", noegle)
            return

        uge["status"] = store.ARBEJDER
        uge["fejlbesked"] = ""
        store.gem_uge(uge)

    try:
        tilbud = await rema.hent_tilbud()
    except Exception as e:
        return _fejl(noegle, "Kunne ikke hente tilbud fra REMA: {}".format(e))

    # Er kilden brudt, får vi typisk 0 eller ganske få varer. Så er det bedre
    # at sige det højt end at bede AI'en om at digte en madplan.
    if len(tilbud) < config.MIN_TILBUD:
        return _fejl(
            noegle,
            "Fandt kun {} tilbud. REMA's API er sandsynligvis ændret.".format(len(tilbud)),
        )

    try:
        forslag = await ai.foreslaa_retter(tilbud, config.hent_praeferencer())
    except Exception as e:
        return _fejl(noegle, "Kunne ikke lave forslag: {}".format(e))

    if not forslag:
        return _fejl(noegle, "Ingen brugbare forslag kom retur. Prøv igen.")

    # Hver ret starter på husstandens standard og justeres derefter pr. ret
    standard = config.hent_praeferencer().get("standard_portioner", 4)
    for ret in forslag:
        ret["portioner"] = standard

    uge = store.hent_uge(noegle)
    uge.update(
        {
            "tilbud": tilbud,
            "forslag": forslag,
            "valgt": [],
            "madplan": {},
            "afkrydset": {},
            "status": store.VAELGER,
        }
    )
    # uge["egne"] bevidst urørt: familiens egne retter skal overleve at man
    # henter nye forslag.
    store.gem_uge(uge)
    log.info("Klar med %d forslag for %s", len(forslag), noegle)

    besked = "Ugens {} madforslag er klar. Vælg hvad I vil have.".format(len(forslag))
    await notify.send(besked)
    await push.send("Nye madforslag", besked, "/uge/{}".format(noegle))


async def lav_madplan(noegle: str | None = None) -> None:
    async with _laas:
        noegle = noegle or store.uge_noegle()
        uge = store.hent_uge(noegle)

        if uge["status"] == store.ARBEJDER:
            return
        if not uge["forslag"]:
            return _fejl(noegle, "Der er ingen forslag at lave en madplan ud fra.")

        egne = [e for e in (uge.get("egne") or []) if e.get("valgt")]
        valgte_idx = sorted(uge.get("valgt") or [])
        if not valgte_idx and not egne:
            # Ingen har valgt — tag de første frem for at droppe ugen.
            valgte_idx = list(range(min(config.ANTAL_RETTER, len(uge["forslag"]))))

        uge["valgt"] = valgte_idx
        uge["status"] = store.ARBEJDER
        uge["fejlbesked"] = ""
        store.gem_uge(uge)

    valgte = [uge["forslag"][i] for i in valgte_idx] + [
        {**e, "egen": True} for e in egne
    ]

    try:
        madplan = await ai.lav_madplan(valgte, uge["tilbud"], config.hent_praeferencer())
    except Exception as e:
        return _fejl(noegle, "Kunne ikke lave opskrifterne: {}".format(e))

    uge = store.hent_uge(noegle)
    uge["madplan"] = madplan
    uge["afkrydset"] = {}
    uge["status"] = store.KLAR
    store.gem_uge(uge)
    store.tilfoej_historik(noegle, uge["forslag"], valgte_idx, uge.get("egne"))

    antal = sum(len(g.get("varer", [])) for g in madplan.get("indkoebsliste", []))
    log.info("Madplan klar for %s: %d retter, %d varer", noegle, len(valgte), antal)

    besked = "Madplanen for uge {} er klar: {} retter og {} varer på indkøbslisten.".format(
        store.uge_nummer(noegle), len(valgte), antal
    )
    await notify.send(besked)
    await push.send("Madplanen er klar", besked, "/uge/{}".format(noegle))


async def deadline() -> None:
    """Søndag aften: luk ugen hvis ingen har trykket selv."""
    uge = store.hent_uge()
    if uge["status"] == store.VAELGER:
        log.info("Deadline nået — laver madplan automatisk")
        await lav_madplan(uge["uge"])


def _fejl(noegle: str, besked: str) -> None:
    log.error("%s: %s", noegle, besked)
    uge = store.hent_uge(noegle)
    uge["status"] = store.FEJL
    uge["fejlbesked"] = besked
    store.gem_uge(uge)
