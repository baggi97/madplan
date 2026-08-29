"""De to Claude-kald.

Kald 1: 10 retforslag ud fra ugens tilbud.
Kald 2: fulde opskrifter + samlet indkøbsliste for de valgte retter.

Begge bruger tool-use til at fremtvinge gyldig JSON, og forslagene valideres
bagefter mod de faktiske tilbuds-ID'er. Uden det trin finder modellen før eller
siden på et tilbud der ikke findes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

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
                        "kategori": {
                            "type": "string",
                            "enum": ["koed", "fisk", "vegetar"],
                            "description": (
                                "'fisk' hvis retten indeholder fisk eller skaldyr. "
                                "'vegetar' hvis den er helt uden kød og fisk. "
                                "Ellers 'koed'."
                            ),
                        },
                        "koekken": {
                            "type": "string",
                            "enum": [
                                "dansk", "italiensk", "asiatisk", "mexicansk",
                                "mellemoestlig", "andet",
                            ],
                            "description": (
                                "Rettens køkken. 'asiatisk' dækker bl.a. thai, "
                                "kinesisk, japansk, indisk og vietnamesisk."
                            ),
                        },
                    },
                    "required": [
                        "navn", "beskrivelse", "tid_min", "pris_pr_portion",
                        "tilbuds_ids", "kategori", "koekken",
                    ],
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


# Forbigående fejl må ikke koste hele ugen. Den ugentlige kørsel er søndag
# kl. 8, og går den i fejl, står madplanen tom til nogen opdager det. Set i
# praksis: en 503 "credential validation failed" hvor nøglen var helt i orden.
FORSOEG = 3
PAUSER = (3, 12)          # sekunder mellem forsøg
SAMLET_FRIST = 300        # start ikke et nyt forsøg efter så mange sekunder


def _forbigaaende(r: httpx.Response) -> bool:
    """429 og 5xx går som regel væk af sig selv. 4xx gør ikke."""
    return r.status_code == 429 or r.status_code >= 500


def _pause(nr: int, r: "httpx.Response | None") -> float:
    if r is not None:
        efter = r.headers.get("retry-after")
        if efter:
            try:
                return min(float(efter), 60.0)
            except ValueError:
                pass
    return PAUSER[min(nr, len(PAUSER) - 1)]


async def _kald(system: str, besked: str, vaerktoej: dict, maks_tokens: int) -> dict:
    krop = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": maks_tokens,
        "system": system,
        "messages": [{"role": "user", "content": besked}],
        "tools": [vaerktoej],
        "tool_choice": {"type": "tool", "name": vaerktoej["name"]},
    }
    hoveder = {**HEADERS, "x-api-key": config.ANTHROPIC_API_KEY}
    start = asyncio.get_event_loop().time()
    sidste = None

    for nr in range(FORSOEG):
        if nr:
            pause = _pause(nr - 1, sidste if isinstance(sidste, httpx.Response) else None)
            if asyncio.get_event_loop().time() - start + pause > SAMLET_FRIST:
                log.error("Opgiver — samlet frist på %d s er brugt", SAMLET_FRIST)
                break
            log.warning(
                "Forsøg %d af %d mislykkedes — prøver igen om %.0f s", nr, FORSOEG, pause
            )
            await asyncio.sleep(pause)

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                r = await client.post(config.ANTHROPIC_URL, headers=hoveder, json=krop)
        except httpx.TransportError as e:      # timeout og netværksfejl
            sidste = e
            continue

        if _forbigaaende(r):
            log.error("Anthropic-fejl %s: %s", r.status_code, r.text[:300])
            sidste = r
            continue
        if r.status_code >= 400:
            raise RuntimeError(_fejlbesked(r))  # blivende — nytter ikke at prøve igen

        return _udtraek_vaerktoej(r.json())

    if isinstance(sidste, httpx.Response):
        raise RuntimeError(_fejlbesked(sidste))
    raise RuntimeError(
        "Kunne ikke nå Anthropic efter {} forsøg ({}). Tjek nettet og prøv igen.".format(
            FORSOEG, type(sidste).__name__ if sidste else "ukendt"
        )
    )


def _udtraek_vaerktoej(svar: dict) -> dict:
    for blok in svar.get("content", []):
        if blok.get("type") == "tool_use":
            return blok["input"]
    raise RuntimeError("Modellen returnerede ikke det forventede værktøjskald")


def _fejlbesked(r: httpx.Response) -> str:
    """Anthropic's egen besked, så familien ser noget de kan handle på.

    `raise_for_status()` giver kun "Client error '401 Unauthorized' for url
    ...", hvilket ikke fortæller nogen hvad de skal gøre.
    """
    log.error("Anthropic-fejl %s: %s", r.status_code, r.text[:500])
    try:
        fejl = (r.json() or {}).get("error") or {}
        art, besked = fejl.get("type", ""), fejl.get("message", "")
    except Exception:
        art, besked = "", ""

    if r.status_code == 401 or art == "authentication_error":
        return "Anthropic afviste API-nøglen. Tjek ANTHROPIC_API_KEY i .env."
    if r.status_code == 400 and art == "invalid_request_error":
        return "Anthropic afviste forespørgslen: {}".format(besked)
    if r.status_code == 429:
        return "Anthropic's hastighedsgrænse er nået. Prøv igen om lidt."
    if art == "billing_error" or "credit" in besked.lower():
        return "Der er ikke flere credits på Anthropic-kontoen."
    if r.status_code >= 500:
        return "Anthropic har problemer lige nu ({}). Prøv igen senere.".format(
            r.status_code
        )
    return besked or "Anthropic svarede {}".format(r.status_code)


KATEGORIER = ("koed", "fisk", "vegetar")
KOEKKENER = ("dansk", "italiensk", "asiatisk", "mexicansk", "mellemoestlig", "andet")


def _heltal(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _maerkater(ret: dict, regler: dict) -> list[str]:
    """Alle lofter denne ret tæller med i.

    En ret kan bære flere mærkater — en dyr thairet er både 'asiatisk' og
    'dyre'. Rammer den bare ét fyldt loft, ryger den.
    """
    m = []
    if ret.get("kategori") in KATEGORIER:
        m.append(ret["kategori"])
    if ret.get("koekken") in KOEKKENER:
        m.append(ret["koekken"])
    graense = regler.get("dyr_over_kr")
    if isinstance(graense, (int, float)) and not isinstance(graense, bool):
        try:
            if float(ret.get("pris_pr_portion") or 0) > float(graense):
                m.append("dyre")
        except (TypeError, ValueError):
            pass
    return m


def _udpak_retter(svar: dict) -> list:
    """Henter rettelisten ud af modellens svar.

    Modellen har i praksis pakket hele svaret som én JSON-streng i stedet for
    et objekt. Indholdet er gyldigt — det er kun kodet én gang for meget — så
    vi pakker ud i stedet for at smide et betalt kald væk. Uden det her løber
    valideringen hen over strengen tegn for tegn og kasserer 250 "retter".
    """
    retter = svar.get("retter") if isinstance(svar, dict) else None

    if isinstance(retter, str):
        try:
            indre = json.loads(retter)
        except json.JSONDecodeError:
            log.warning("'retter' kom som en streng der ikke er JSON — kasseres")
            return []
        retter = indre.get("retter") if isinstance(indre, dict) else indre
        log.warning("Modellen dobbeltkodede svaret som JSON-streng — pakket ud igen")

    if not isinstance(retter, list):
        log.warning("Forventede en liste af retter, fik %s", type(retter).__name__)
        return []
    return retter


def _regeltekst(regler: dict) -> str:
    """Bygger kravlisten til prompten ud fra kostreglerne i praeferencer.yaml.

    Går generisk gennem alle `maks_*`, så en ny regel i YAML'en virker uden
    kodeændring.
    """
    linjer = ["- {}".format(x) for x in (regler.get("profil") or [])]
    for noegle in sorted(regler):
        if not noegle.startswith("maks_") or not _heltal(regler[noegle]):
            continue
        loft, maerkat = regler[noegle], noegle[len("maks_"):]
        ret_ord = "ret" if loft == 1 else "retter"
        if maerkat == "dyre":
            linjer.append(
                "- Højst {} {} der koster over {:.0f} kr. pr. portion.".format(
                    loft, ret_ord, float(regler.get("dyr_over_kr") or 0)
                )
            )
        else:
            linjer.append(
                "- Højst {} {} af typen '{}'.".format(loft, ret_ord, maerkat)
            )
    if not linjer:
        return ""
    return "Krav til retterne:\n" + "\n".join(linjer) + "\n\n"


def _haandhaev_lofter(retter: list[dict], regler: dict) -> list[dict]:
    """Trimmer forslag der overskrider et loft.

    Modellen får kravene i prompten, men overholder dem ikke pålideligt —
    derfor tælles der efter her. Rækkefølgen bevares, så det er de senere
    retter i en overfyldt kategori der ryger.
    """
    if not regler:
        return retter
    talt: dict[str, int] = {}
    ok = []
    for ret in retter:
        maerkater = _maerkater(ret, regler)
        fyldt = next(
            (
                m for m in maerkater
                if _heltal(regler.get("maks_" + m))
                and talt.get(m, 0) >= regler["maks_" + m]
            ),
            None,
        )
        if fyldt:
            log.warning(
                "Kasserer '%s' — loftet på %d '%s' er nået",
                ret.get("navn"), regler["maks_" + fyldt], fyldt,
            )
            continue
        for m in maerkater:
            talt[m] = talt.get(m, 0) + 1
        ok.append(ret)
    return ok


def _praeferencetekst(praef: dict) -> str:
    if not praef:
        return "Ingen særlige præferencer angivet."
    return json.dumps(praef, ensure_ascii=False, indent=2)


async def foreslaa_retter(tilbud: list[dict], praef: dict) -> list[dict]:
    gyldige = {t["id"] for t in tilbud}
    undgaa = store.seneste_retter(6)
    signal = store.praeferencesignal()
    regler = praef.get("kostregler") or {}

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
        "5. Foreslå ikke noget der ligner retterne på 'undgå'-listen.\n"
        "6. Overhold alt under 'Krav til retterne'. Sæt `kategori` og "
        "`koekken` ærligt — det er dem kravene tælles på.\n"
        "7. Byg ikke retter på noget fra 'allergier' eller 'kan_vi_ikke_lide'. "
        "Nævn det heller ikke: ingen navne eller beskrivelser som "
        "'postejfri', 'uden nødder' eller 'undgået denne uge'. Find på noget "
        "andet, og lad som om varen ikke findes."
    )

    besked = (
        f"Ugens tilbud i REMA 1000:\n{rema.til_prompt_linjer(tilbud)}\n\n"
        f"{_regeltekst(regler)}"
        f"Husstandens præferencer:\n{_praeferencetekst(praef)}\n\n"
        f"Serveret de sidste 6 uger (undgå disse):\n"
        f"{', '.join(undgaa) if undgaa else 'ingen historik endnu'}\n\n"
        f"Valgt ofte tidligere: {', '.join(signal['ofte_valgt']) or 'intet endnu'}\n"
        f"Fravalgt ofte tidligere: {', '.join(signal['ofte_fravalgt']) or 'intet endnu'}\n\n"
        f"Foreslå præcis {config.ANTAL_FORSLAG} retter."
    )

    svar = await _kald(system, besked, VAERKTOEJ_FORSLAG, 4000)
    retter = _haandhaev_lofter(_valider_forslag(_udpak_retter(svar), gyldige), regler)

    # Fik vi for få gyldige retter, beder vi om erstatninger én gang.
    if len(retter) < config.ANTAL_FORSLAG:
        mangler = config.ANTAL_FORSLAG - len(retter)
        log.warning("Kun %d gyldige forslag, beder om %d mere", len(retter), mangler)
        brugt: dict[str, int] = {}
        for r in retter:
            for m in _maerkater(r, regler):
                brugt[m] = brugt.get(m, 0) + 1
        fyldte = [
            n[len("maks_"):] for n in sorted(regler)
            if n.startswith("maks_") and _heltal(regler[n])
            and brugt.get(n[len("maks_"):], 0) >= regler[n]
        ]
        ekstra_besked = (
            besked
            + f"\n\nDisse retter er allerede foreslået, lav {mangler} ANDRE:\n"
            + ", ".join(r["navn"] for r in retter)
            + (
                "\n\nKategorierne {} er fyldt op — foreslå ikke flere af dem.".format(
                    " og ".join("'{}'".format(k) for k in fyldte)
                )
                if fyldte else ""
            )
        )
        svar2 = await _kald(system, ekstra_besked, VAERKTOEJ_FORSLAG, 4000)
        retter = _haandhaev_lofter(
            retter + _valider_forslag(_udpak_retter(svar2), gyldige), regler
        )

    return retter[: config.ANTAL_FORSLAG]


# Felter skabelonerne og `web._beriget()` regner med at have
PAAKRAEVEDE_FELTER = ("navn", "beskrivelse", "tid_min", "pris_pr_portion")


def _valider_forslag(retter: list[dict], gyldige_ids: set[str]) -> list[dict]:
    """Smider retter væk der ikke er brugbare.

    Skemaet lover objekter med alle felter udfyldt, men det er set svigte i
    praksis — modellen har returneret en ren streng i listen. Går sådan én
    igennem, vælter hele ugen med en uforståelig AttributeError.
    """
    ok = []
    for ret in retter:
        if not isinstance(ret, dict):
            log.warning("Kasserer et forslag der ikke er et objekt: %.80r", ret)
            continue
        mangler = [f for f in PAAKRAEVEDE_FELTER if ret.get(f) in (None, "")]
        if mangler:
            log.warning("Kasserer '%s' — mangler felter: %s", ret.get("navn"), mangler)
            continue
        if not isinstance(ret.get("tilbuds_ids"), list):
            log.warning("Kasserer '%s' — tilbuds_ids er ikke en liste", ret.get("navn"))
            continue
        ids = [str(i) for i in ret["tilbuds_ids"]]
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


def _basisvaremoenster(altid: list[str]) -> "re.Pattern | None":
    """Mønster der matcher varer husstanden altid har hjemme.

    Korte ord kun som helt ord — 'mel' må ikke fange 'melon'. Ord på fem
    tegn og derover også som forstavelse, så 'pasta' fanger 'pastaskruer'.
    Flertals-'er' trimmes af stammen, så 'bouillonterninger' i YAML'en også
    fanger 'bouillonterning' i ental.
    """
    dele = []
    for a in altid:
        a = str(a).strip()
        if not a:
            continue
        if len(a) >= 5:
            stamme = a[:-2] if a.endswith("er") and len(a) > 6 else a
            dele.append(re.escape(stamme) + r"\w*")
        else:
            dele.append(re.escape(a))
    if not dele:
        return None
    return re.compile(r"\b(" + "|".join(dele) + r")\b", re.IGNORECASE)


def _fjern_basisvarer(madplan: dict, praef: dict) -> dict:
    """Luger varer fra 'har_altid_hjemme' ud af indkøbslisten.

    Prompten beder allerede om det, men modellen glemmer nogle af dem —
    målt til 3 ud af 19 varer. Samme lærestreg som med kostreglerne: bed om
    det i prompten, og ryd op bagefter.
    """
    moenster = _basisvaremoenster(praef.get("har_altid_hjemme") or [])
    if not moenster:
        return madplan
    for gruppe in madplan.get("indkoebsliste") or []:
        beholdt = []
        for vare in gruppe.get("varer") or []:
            navn = str(vare.get("vare", ""))
            if moenster.search(navn):
                log.info("Fjerner '%s' fra listen — står i har_altid_hjemme", navn)
                continue
            beholdt.append(vare)
        gruppe["varer"] = beholdt
    madplan["indkoebsliste"] = [
        g for g in (madplan.get("indkoebsliste") or []) if g.get("varer")
    ]
    return madplan


async def lav_madplan(valgte: list[dict], tilbud: list[dict], praef: dict) -> dict:
    efter_id = {t["id"]: t for t in tilbud}
    standard = praef.get("standard_portioner", 4)

    linjer = []
    for ret in valgte:
        portioner = ret.get("portioner") or standard
        linjer.append("- {} — til {} personer".format(ret["navn"], portioner))
        if ret.get("beskrivelse"):
            linjer.append("  {}".format(ret["beskrivelse"]))
        brugte = [efter_id[i] for i in ret.get("tilbuds_ids") or [] if i in efter_id]
        if brugte:
            linjer.append(
                "  På tilbud: "
                + "; ".join(
                    f"{b['navn']} ({b['detalje']}) {b['pris']:.2f} kr." for b in brugte
                )
            )
        elif ret.get("egen"):
            linjer.append("  Familiens eget ønske — der er ingen tilbud knyttet til den.")

    system = (
        "Du skriver opskrifter til en dansk husstand. Skriv klart og kort, som "
        "en god madblog — ingen lange indledninger. Brug metriske mål og danske "
        "ingrediensnavne. Svar altid på dansk.\n\n"
        "Indkøbslisten skal være SAMLET på tværs af alle retter (læg ens varer "
        "sammen — ikke 'løg' tre gange) og grupperet efter butiksafdeling i den "
        "rækkefølge man går gennem en REMA: Frugt & grønt, Brød, Køl, Mejeri, "
        "Ost, Kød & fisk, Frost, Kolonial. Marker hvilke varer der er på tilbud.\n\n"
        "Retterne kan have forskelligt antal personer. Indkøbslisten skal "
        "dække summen af dem alle."
    )

    besked = (
        "Skriv opskrifter til disse retter. Hver ret har sit eget antal "
        "personer — brug præcis det tal i `portioner`, og skalér mængderne "
        "efter det:\n\n"
        + "\n".join(linjer)
        + f"\n\nHusstandens præferencer:\n{_praeferencetekst(praef)}\n\n"
        "Antag at husstanden har salt, peber, olie, smør og almindelige tørre "
        "krydderier — dem skal du ikke skrive på indkøbslisten. Det samme "
        "gælder alt under 'har_altid_hjemme' i præferencerne: brug det gerne "
        "i opskrifterne, men skriv det ikke på listen."
    )

    madplan = await _kald(system, besked, VAERKTOEJ_MADPLAN, 8000)
    return _fjern_basisvarer(madplan, praef)
