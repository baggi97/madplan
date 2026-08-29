"""Filbaseret state. Én fil pr. uge + en historikfil."""
from __future__ import annotations

import json
from datetime import date, datetime
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
        "madplan": {},      # opskrifter + indkoebsliste
        "afkrydset": {},    # vare-nøgle -> True
    }


def hent_uge(noegle: str | None = None) -> dict:
    noegle = noegle or uge_noegle()
    uge = _laes("uge-{}.json".format(noegle), tom_uge(noegle))
    for felt, standard in tom_uge(noegle).items():
        uge.setdefault(felt, standard)
    return uge


def gem_uge(uge: dict) -> None:
    uge["opdateret"] = nu().isoformat()
    _skriv("uge-{}.json".format(uge["uge"]), uge)


def alle_uger() -> list[str]:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    noegler = [
        p.stem[4:] for p in config.DATA_DIR.glob("uge-*.json") if p.stem.startswith("uge-")
    ]
    return sorted(noegler, reverse=True)


# --- historik ---------------------------------------------------------

def hent_historik() -> list[dict]:
    return _laes("historik.json", [])


def tilfoej_historik(noegle: str, forslag: list[dict], valgte_idx: list[int]) -> None:
    hist = [h for h in hent_historik() if h["uge"] != noegle]
    hist.append(
        {
            "uge": noegle,
            "dato": nu().date().isoformat(),
            "valgt": [f["navn"] for i, f in enumerate(forslag) if i in valgte_idx],
            "fravalgt": [f["navn"] for i, f in enumerate(forslag) if i not in valgte_idx],
        }
    )
    hist = sorted(hist, key=lambda h: h["uge"])[-52:]
    _skriv("historik.json", hist)


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
