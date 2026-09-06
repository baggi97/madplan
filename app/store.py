"""Filbaseret state. Én fil pr. uge + en historikfil."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

TZINFO = ZoneInfo(config.TZ)

# Ugens livscyklus
TOM = "tom"            # intet hentet endnu
ARBEJDER = "arbejder"  # AI kører — websitet poller
VAELGER = "vaelger"    # forslag klar, familien vælger
KLAR = "klar"          # madplan og indkøbsliste findes
FEJL = "fejl"


def nu() -> datetime:
    return datetime.now(TZINFO)


def uge_noegle(d: date | None = None) -> str:
    d = d or nu().date()
    aar, uge, _ = d.isocalendar()
    return "{}-W{:02d}".format(aar, uge)


def planuge(d: date | None = None) -> str:
    """Nøglen for den uge madplanen gælder for.

    Ikke det samme som "den ISO-uge vi er i". Planen laves søndag morgen og
    gælder fra mandag, så søndag hører til den **kommende** uge — ikke den der
    slutter samme dag.

    Konkret: ISO-ugen for i morgen. Mandag til lørdag er det indeværende uge;
    kun søndag ruller den frem.

    Uden det skrev søndagskørslen til den uge der lige var gået, fandt de
    forslag der allerede lå der, og meldte "forslag findes allerede" i stedet
    for at planlægge den kommende uge. Set i drift søndag 2026-09-06.
    """
    d = d or nu().date()
    return uge_noegle(d + timedelta(days=1))


def uge_nummer(noegle: str) -> str:
    return noegle.split("-W")[-1].lstrip("0")


def _sti(navn: str) -> Path:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR / navn


def _laes(navn: str, standard):
    sti = _sti(navn)
    if not sti.exists():
        return standard
    try:
        return json.loads(sti.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return standard


def _skriv(navn: str, data) -> None:
    sti = _sti(navn)
    midlertidig = sti.with_suffix(sti.suffix + ".tmp")
    midlertidig.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    midlertidig.replace(sti)  # atomisk, så vi aldrig får en halv fil


def tom_uge(noegle: str) -> dict:
    return {
        "uge": noegle,
        "status": TOM,
        "fejlbesked": "",
        "opdateret": nu().isoformat(),
        "tilbud": [],
        "forslag": [],
        "valgt": [],        # indeks i forslag-listen
        "egne": [],         # familiens egne retter: {navn, portioner, valgt}
        "madplan": {},      # opskrifter + indkoebsliste
        "afkrydset": {},    # vare-nøgle -> True
    }


def hent_uge(noegle: str | None = None) -> dict:
    noegle = noegle or planuge()
    uge = _laes("uge-{}.json".format(noegle), tom_uge(noegle))
    for felt, standard in tom_uge(noegle).items():
        uge.setdefault(felt, standard)
    return uge


def gem_uge(uge: dict) -> None:
    uge["opdateret"] = nu().isoformat()
    _skriv("uge-{}.json".format(uge["uge"]), uge)


def antal_valgt(uge: dict) -> int:
    """Valgte forslag plus familiens egne retter.

    Ligger her og ikke i web.py, fordi flow.paamindelse() også skal bruge den
    og web importerer flow — ikke omvendt.
    """
    return len(uge.get("valgt") or []) + sum(
        1 for e in (uge.get("egne") or []) if e.get("valgt")
    )


def alle_uger() -> list[str]:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    noegler = [
        p.stem[4:] for p in config.DATA_DIR.glob("uge-*.json") if p.stem.startswith("uge-")
    ]
    return sorted(noegler, reverse=True)


# --- historik ---------------------------------------------------------

def hent_historik() -> list[dict]:
    return _laes("historik.json", [])


def tilfoej_historik(
    noegle: str,
    forslag: list[dict],
    valgte_idx: list[int],
    egne: list[dict] | None = None,
) -> None:
    # Egne retter tæller som valgt — de er jo aktivt tilføjet af familien.
    egne_navne = [e["navn"] for e in (egne or []) if e.get("valgt")]
    hist = [h for h in hent_historik() if h["uge"] != noegle]
    hist.append(
        {
            "uge": noegle,
            "dato": nu().date().isoformat(),
            "valgt": [f["navn"] for i, f in enumerate(forslag) if i in valgte_idx]
            + egne_navne,
            "fravalgt": [f["navn"] for i, f in enumerate(forslag) if i not in valgte_idx],
        }
    )
    hist = sorted(hist, key=lambda h: h["uge"])[-52:]
    _skriv("historik.json", hist)


# --- push-abonnementer ------------------------------------------------

def hent_abonnementer() -> list[dict]:
    """Browserne der har sagt ja til push. Én post pr. browser, ikke pr. person."""
    return _laes("abonnementer.json", [])


def gem_abonnementer(abonnementer: list[dict]) -> None:
    _skriv("abonnementer.json", abonnementer)


def tilfoej_abonnement(abonnement: dict) -> bool:
    """Returnerer True hvis det var nyt. Endpoint'et er browserens identitet."""
    alle = hent_abonnementer()
    if any(a.get("endpoint") == abonnement.get("endpoint") for a in alle):
        return False
    alle.append(abonnement)
    gem_abonnementer(alle)
    return True


def fjern_abonnement(endepunkt: str) -> bool:
    alle = hent_abonnementer()
    tilbage = [a for a in alle if a.get("endpoint") != endepunkt]
    if len(tilbage) == len(alle):
        return False
    gem_abonnementer(tilbage)
    return True


# --- bedømmelser ------------------------------------------------------
#
# "Fravalgt" er et svagt signal: valgte familien fire ud af ti, siger det
# intet om de seks andre. En tommel op eller ned efter måltidet siger noget.

OP, NED = "op", "ned"


def saet_bedoemmelse(noegle: str, navn: str, vurdering: str | None) -> None:
    """Skriver bedømmelsen i historikken for den uge retten blev spist i.

    Den hører hjemme i historikken og ikke i ugefilen, fordi det er derfra
    signalet til fremtidige forslag læses. `None` fjerner bedømmelsen igen.
    """
    hist = hent_historik()
    for h in hist:
        if h["uge"] != noegle:
            continue
        b = h.get("bedoemt") or {}
        if vurdering is None:
            b.pop(navn, None)
        else:
            b[navn] = vurdering
        h["bedoemt"] = b
        _skriv("historik.json", hist)
        return


def bedoemmelser(noegle: str) -> dict:
    for h in hent_historik():
        if h["uge"] == noegle:
            return h.get("bedoemt") or {}
    return {}


def nedstemte_retter() -> list[str]:
    """Retter familien har sagt fra til. Uden tidsgrænse — modsat
    gentagelsesfilteret, for en ret man ikke kunne lide bliver ikke bedre
    af at der går fire uger."""
    navne = []
    for h in hent_historik():
        for navn, vurdering in (h.get("bedoemt") or {}).items():
            if vurdering == NED:
                navne.append(navn)
    return navne


def yndlingsretter(antal_uger: int = 26) -> list[str]:
    navne = []
    for h in hent_historik()[-antal_uger:]:
        for navn, vurdering in (h.get("bedoemt") or {}).items():
            if vurdering == OP:
                navne.append(navn)
    return navne


def seneste_retter(antal_uger: int = 6) -> list[str]:
    """Retter serveret for nylig — bruges til at undgå gentagelser."""
    navne: list[str] = []
    for h in hent_historik()[-antal_uger:]:
        navne.extend(h.get("valgt", []))
    return navne


def praeferencesignal(antal_uger: int = 12) -> dict:
    """Grov optælling af hvad der bliver valgt og fravalgt over tid."""
    valgt: dict[str, int] = {}
    fravalgt: dict[str, int] = {}
    for h in hent_historik()[-antal_uger:]:
        for n in h.get("valgt", []):
            valgt[n] = valgt.get(n, 0) + 1
        for n in h.get("fravalgt", []):
            fravalgt[n] = fravalgt.get(n, 0) + 1
    return {"ofte_valgt": valgt, "ofte_fravalgt": fravalgt}
