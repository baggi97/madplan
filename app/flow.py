"""Ugens forløb.

hent_forslag()   → hent tilbud, foreslå retter (søndag morgen eller manuelt)
lav_madplan()    → opskrifter + indkøbsliste ud fra de valgte retter
deadline()       → kører lav_madplan hvis ingen har trykket selv
"""
from __future__ import annotations

import asyncio
import logging

from . import ai, config, push, rema, store

log = logging.getLogger(__name__)

# Sikrer at to samtidige klik ikke starter det samme AI-kald to gange
_laas = asyncio.Lock()


async def hent_forslag(gennemtving: bool = False) -> None:
    async with _laas:
        noegle = store.planuge()
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
        return await _fejl(noegle, "Kunne ikke hente tilbud fra REMA: {}".format(e))

    # Er kilden brudt, får vi typisk 0 eller ganske få varer. Så er det bedre
    # at sige det højt end at bede AI'en om at digte en madplan.
    if len(tilbud) < config.MIN_TILBUD:
        return await _fejl(
            noegle,
            "Fandt kun {} tilbud. REMA's API er sandsynligvis ændret.".format(len(tilbud)),
        )

    # Regnskab over hvad kæden kasserede, så websitet kan sige hvorfor der
    # kom færre retter end bedt om — i stedet for at grunden kun står i en
    # log inde i containeren.
    regnskab: dict = {}
    try:
        forslag = await ai.foreslaa_retter(tilbud, config.hent_praeferencer(), regnskab)
    except Exception as e:
        return await _fejl(noegle, "Kunne ikke lave forslag: {}".format(e))

    if not forslag:
        return await _fejl(noegle, "Ingen brugbare forslag kom retur. Prøv igen.")

    # Hver ret starter på husstandens standard og justeres derefter pr. ret
    standard = config.hent_praeferencer().get("standard_portioner", 4)
    for ret in forslag:
        ret["portioner"] = standard

    uge = store.hent_uge(noegle)
    uge.update(
        {
            "tilbud": tilbud,
            "forslag": forslag,
            "frasorteret": regnskab,
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

    await push.send(
        "Nye madforslag",
        "Ugens {} madforslag er klar. Vælg hvad I vil have.".format(len(forslag)),
        "/uge/{}".format(noegle),
    )


async def lav_madplan(noegle: str | None = None) -> None:
    mangler_forslag = False
    async with _laas:
        noegle = noegle or store.planuge()
        uge = store.hent_uge(noegle)

        if uge["status"] == store.ARBEJDER:
            return
        if not uge["forslag"]:
            # Fejlen meldes uden for låsen: _fejl sender en push, og et
            # netværkskald må ikke holde AI-låsen.
            mangler_forslag = True

        egne = [e for e in (uge.get("egne") or []) if e.get("valgt")]
        valgte_idx = sorted(uge.get("valgt") or [])
        if not valgte_idx and not egne:
            # Ingen har valgt — tag de første frem for at droppe ugen.
            valgte_idx = list(range(min(config.ANTAL_RETTER, len(uge["forslag"]))))

        if not mangler_forslag:
            uge["valgt"] = valgte_idx
            uge["status"] = store.ARBEJDER
            uge["fejlbesked"] = ""
            store.gem_uge(uge)

    if mangler_forslag:
        return await _fejl(noegle, "Der er ingen forslag at lave en madplan ud fra.")

    valgte = [uge["forslag"][i] for i in valgte_idx] + [
        {**e, "egen": True} for e in egne
    ]

    try:
        madplan = await ai.lav_madplan(valgte, uge["tilbud"], config.hent_praeferencer())
    except Exception as e:
        return await _fejl(noegle, "Kunne ikke lave opskrifterne: {}".format(e))

    uge = store.hent_uge(noegle)
    uge["madplan"] = madplan
    uge["afkrydset"] = {}
    uge["status"] = store.KLAR
    store.gem_uge(uge)
    store.tilfoej_historik(noegle, uge["forslag"], valgte_idx, uge.get("egne"))

    antal = sum(len(g.get("varer", [])) for g in madplan.get("indkoebsliste", []))
    log.info("Madplan klar for %s: %d retter, %d varer", noegle, len(valgte), antal)

    await push.send(
        "Madplanen er klar",
        "Madplanen for uge {} er klar: {} retter og {} varer på indkøbslisten.".format(
            store.uge_nummer(noegle), len(valgte), antal
        ),
        "/uge/{}".format(noegle),
    )


async def deadline() -> None:
    """Søndag aften: luk ugen hvis ingen har trykket selv."""
    uge = store.hent_uge()
    if uge["status"] == store.VAELGER:
        log.info("Deadline nået — laver madplan automatisk")
        await lav_madplan(uge["uge"])


async def _fejl(noegle: str, besked: str) -> None:
    """Skriver fejlen til ugen og siger det højt.

    Uden pushen står en fejlet søndagskørsel og venter på at nogen tilfældigt
    åbner websitet. Vi ramte en 503 fra Anthropic hvor nøglen var helt i
    orden — og hele pointen med den automatiske kørsel er at ingen skal
    holde øje.
    """
    log.error("%s: %s", noegle, besked)
    uge = store.hent_uge(noegle)
    uge["status"] = store.FEJL
    uge["fejlbesked"] = besked
    store.gem_uge(uge)
    await push.send("Madplanen fejlede", besked, "/uge/{}".format(noegle))


async def paamindelse() -> None:
    """Sidst på eftermiddagen: puf til familien hvis ingen har valgt endnu.

    Kører før deadline, så der er tid til at nå det. Har nogen allerede valgt,
    siger vi ingenting — så er beskeden bare støj.
    """
    uge = store.hent_uge()
    if uge["status"] != store.VAELGER:
        return
    if store.antal_valgt(uge):
        return
    log.info("Ingen har valgt endnu — sender påmindelse")
    await push.send(
        "Husk at vælge",
        "Ingen har valgt retter endnu. Madplanen laves automatisk kl. 18.",
        "/uge/{}".format(uge["uge"]),
    )
