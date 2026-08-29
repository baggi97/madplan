"""De to Claude-kald.

Kald 1: 10 retforslag ud fra ugens tilbud.
Kald 2: fulde opskrifter + samlet indkøbsliste for de valgte retter.

Begge bruger tool-use til at fremtvinge gyldig JSON, og forslagene valideres
bagefter mod de faktiske tilbuds-ID'er. Uden det trin finder modellen før eller
siden på et tilbud der ikke findes.
"""
from __future__ import annotations

import json
import logging

import httpx

from . import config, rema, store

log = logging.getLogger(__name__)

HEADERS = {
    "content-type": "application/json",
    "anthropic-version": "2023-06-01",
}

VAERKTOEJ_FORSLAG = {
    "name": "returner_forslag",
    "description": "Returnerer ugens retforslag.",
    "input_schema": {
        "type": "object",
        "properties": {
            "retter": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "navn": {"type": "string", "description": "Rettens navn på dansk"},
                        "beskrivelse": {
                            "type": "string",
                            "description": "Én sætning der sælger retten. Maks 25 ord.",
                        },
                        "tid_min": {"type": "integer", "description": "Tilberedningstid i minutter"},
                        "pris_pr_portion": {"type": "number", "description": "Anslået pris i kroner"},
                        "tilbuds_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "ID'er fra tilbudslisten som retten bygger på. Kun ID'er der findes i listen.",
                        },
                    },
                    "required": ["navn", "beskrivelse", "tid_min", "pris_pr_portion", "tilbuds_ids"],
                },
            }
        },
        "required": ["retter"],
    },
}

VAERKTOEJ_MADPLAN = {
    "name": "returner_madplan",
    "description": "Returnerer opskrifter og en samlet indkøbsliste.",
    "input_schema": {
        "type": "object",
        "properties": {
            "opskrifter": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "navn": {"type": "string"},
                        "portioner": {"type": "integer"},
                        "tid_min": {"type": "integer"},
                        "ingredienser": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Fx '500 g hakket oksekød' — mængde først.",
                        },
                        "fremgangsmaade": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Nummererede trin, ét pr. element.",
                        },
                        "tip": {"type": "string", "description": "Valgfrit tip. Kan være tom."},
                    },
                    "required": ["navn", "portioner", "tid_min", "ingredienser", "fremgangsmaade"],
                },
            },
            "indkoebsliste": {
                "type": "array",
                "description": "Samlet på tværs af alle retter, grupperet efter butiksafdeling.",
                "items": {
                    "type": "object",
                    "properties": {
                        "afdeling": {
                            "type": "string",
                            "description": "Fx 'Frugt & grønt', 'Køl', 'Kolonial'",
                        },
                        "varer": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "vare": {"type": "string", "description": "Fx '1 kg gulerødder'"},
                                    "paa_tilbud": {"type": "boolean"},
                                },
                                "required": ["vare", "paa_tilbud"],
                            },
                        },
                    },
                    "required": ["afdeling", "varer"],
                },
            },
        },
        "required": ["opskrifter", "indkoebsliste"],
    },
}


async def _kald(system: str, besked: str, vaerktoej: dict, maks_tokens: int) -> dict:
    krop = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": maks_tokens,
        "system": system,
        "messages": [{"role": "user", "content": besked}],
        "tools": [vaerktoej],
        "tool_choice": {"type": "tool", "name": vaerktoej["name"]},
    }
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            config.ANTHROPIC_URL,
            headers={**HEADERS, "x-api-key": config.ANTHROPIC_API_KEY},
            json=krop,
        )
        if r.status_code >= 400:
            log.error("Anthropic-fejl %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()
        svar = r.json()

    for blok in svar.get("content", []):
        if blok.get("type") == "tool_use":
            return blok["input"]
    raise RuntimeError("Modellen returnerede ikke det forventede værktøjskald")


def _praeferencetekst(praef: dict) -> str:
    if not praef:
        return "Ingen særlige præferencer angivet."
    return json.dumps(praef, ensure_ascii=False, indent=2)


async def foreslaa_retter(tilbud: list[dict], praef: dict) -> list[dict]:
    gyldige = {t["id"] for t in tilbud}
    undgaa = store.seneste_retter(6)
    signal = store.praeferencesignal()

    system = (
        "Du planlægger ugens aftensmad for en dansk husstand ud fra REMA 1000's "
        "aktuelle tilbud. Du foreslår almindelig, realistisk hverdagsmad — ikke "
        "restaurantretter. Svar altid på dansk.\n\n"
        "Regler:\n"
        "1. Hver ret SKAL bygge på 2-4 varer fra tilbudslisten. Resten må gerne "
        "være almindelige basisvarer (ris, pasta, løg, krydderier, mel).\n"
        "2. Du må KUN referere til ID'er der står i tilbudslisten. Find aldrig "
        "varer eller priser på.\n"
        "3. Variation: forskellige proteinkilder og køkkener på tværs af de 10 "
        "forslag. Ikke fem retter med hakket oksekød.\n"
        "4. Mindst halvdelen skal kunne laves på 30 minutter eller mindre.\n"
        "5. Foreslå ikke noget der ligner retterne på 'undgå'-listen."
    )

    besked = (
        f"Ugens tilbud i REMA 1000:\n{rema.til_prompt_linjer(tilbud)}\n\n"
        f"Husstandens præferencer:\n{_praeferencetekst(praef)}\n\n"
        f"Serveret de sidste 6 uger (undgå disse):\n"
        f"{', '.join(undgaa) if undgaa else 'ingen historik endnu'}\n\n"
        f"Valgt ofte tidligere: {', '.join(signal['ofte_valgt']) or 'intet endnu'}\n"
        f"Fravalgt ofte tidligere: {', '.join(signal['ofte_fravalgt']) or 'intet endnu'}\n\n"
        f"Foreslå præcis {config.ANTAL_FORSLAG} retter."
    )

    svar = await _kald(system, besked, VAERKTOEJ_FORSLAG, 4000)
    retter = _valider_forslag(svar.get("retter", []), gyldige)

    # Fik vi for få gyldige retter, beder vi om erstatninger én gang.
    if len(retter) < config.ANTAL_FORSLAG:
        mangler = config.ANTAL_FORSLAG - len(retter)
        log.warning("Kun %d gyldige forslag, beder om %d mere", len(retter), mangler)
        ekstra_besked = (
            besked
            + f"\n\nDisse retter er allerede foreslået, lav {mangler} ANDRE:\n"
            + ", ".join(r["navn"] for r in retter)
        )
        svar2 = await _kald(system, ekstra_besked, VAERKTOEJ_FORSLAG, 4000)
        retter += _valider_forslag(svar2.get("retter", []), gyldige)

    return retter[: config.ANTAL_FORSLAG]


def _valider_forslag(retter: list[dict], gyldige_ids: set[str]) -> list[dict]:
    """Smider retter væk der refererer til tilbud som ikke findes."""
    ok = []
    for ret in retter:
        ids = [str(i) for i in ret.get("tilbuds_ids", [])]
        ukendte = [i for i in ids if i not in gyldige_ids]
        if ukendte:
            log.warning("Kasserer '%s' — ukendte tilbuds-ID'er: %s", ret.get("navn"), ukendte)
            continue
        if not ids:
            log.warning("Kasserer '%s' — ingen tilbud brugt", ret.get("navn"))
            continue
        ret["tilbuds_ids"] = ids
        ok.append(ret)
    return ok


async def lav_madplan(valgte: list[dict], tilbud: list[dict], praef: dict) -> dict:
    efter_id = {t["id"]: t for t in tilbud}
    portioner = praef.get("antal_personer", 4)

    linjer = []
    for ret in valgte:
        brugte = [efter_id[i] for i in ret["tilbuds_ids"] if i in efter_id]
        varer = "; ".join(f"{b['navn']} ({b['detalje']}) {b['pris']:.2f} kr." for b in brugte)
        linjer.append(f"- {ret['navn']}: {ret['beskrivelse']}\n  På tilbud: {varer}")

    system = (
        "Du skriver opskrifter til en dansk husstand. Skriv klart og kort, som "
        "en god madblog — ingen lange indledninger. Brug metriske mål og danske "
        "ingrediensnavne. Svar altid på dansk.\n\n"
        "Indkøbslisten skal være SAMLET på tværs af alle retter (læg ens varer "
        "sammen — ikke 'løg' tre gange) og grupperet efter butiksafdeling i den "
        "rækkefølge man går gennem en REMA: Frugt & grønt, Brød, Køl, Mejeri, "
        "Ost, Kød & fisk, Frost, Kolonial. Marker hvilke varer der er på tilbud."
    )

    besked = (
        f"Skriv opskrifter til {portioner} personer for disse retter:\n\n"
        + "\n".join(linjer)
        + f"\n\nHusstandens præferencer:\n{_praeferencetekst(praef)}\n\n"
        "Antag at husstanden har salt, peber, olie, smør og almindelige tørre "
        "krydderier — dem skal du ikke skrive på indkøbslisten."
    )

    return await _kald(system, besked, VAERKTOEJ_MADPLAN, 8000)
