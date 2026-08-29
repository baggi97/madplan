"""Henter ugens tilbud fra REMA 1000's katalog-API.

Endpointet er det som REMA-appen selv bruger. Det kræver ingen nøgle, men det
er heller ikke en dokumenteret, garanteret API — derfor validerer vi svaret og
larmer hvis det ser forkert ud, i stedet for at køre videre på tom luft.
"""
from __future__ import annotations

import logging
import re

import httpx

from . import config

log = logging.getLogger(__name__)

# Varer der ligger i madafdelingerne, men ikke hører hjemme i en madplan
UDELUK = re.compile(
    r"(slik|chips|sodavand|energidrik|cider|\bøl\b|\bvin\b|spiritus|snack|"
    r"\bis\b|iskage|chokolade|lakrids|tyggegummi|kaffekapsl|cigaret|"
    r"hundefoder|kattefoder|bleer|vaskepulver)",
    re.IGNORECASE,
)


async def hent_tilbud() -> list[dict]:
    """Returnerer en normaliseret liste af madvarer på tilbud."""
    async with httpx.AsyncClient(timeout=90) as client:
        svar = await client.get(
            config.REMA_URL,
            headers={"User-Agent": "madplan/1.0 (privat husholdningsbrug)"},
        )
        svar.raise_for_status()
        afdelinger = svar.json()

    tilbud: list[dict] = []
    for afd in afdelinger:
        if afd.get("id") not in config.MAD_AFDELINGER:
            continue
        for kat in afd.get("categories", []):
            if kat.get("hidden"):
                continue
            for vare in kat.get("items", []):
                normaliseret = _normaliser(vare, afd, kat)
                if normaliseret:
                    tilbud.append(normaliseret)

    # Bedste rabat først — det er dem AI'en skal bygge retter omkring
    tilbud.sort(key=lambda t: t["rabat_pct"], reverse=True)
    log.info("Hentede %d madvarer på tilbud", len(tilbud))
    return tilbud


def _normaliser(vare: dict, afd: dict, kat: dict) -> dict | None:
    pris = vare.get("pricing") or {}
    if not pris.get("is_on_discount"):
        return None

    normal = float(pris.get("normal_price") or 0)
    aktuel = float(pris.get("price") or 0)
    if normal <= 0 or aktuel <= 0 or aktuel >= normal:
        return None  # "annonceret", men ikke reelt billigere

    navn = (vare.get("name") or "").strip()
    detalje = (vare.get("underline") or "").strip()
    if UDELUK.search(navn + " " + detalje + " " + str(kat.get("name", ""))):
        return None

    return {
        "id": str(vare["id"]),
        "navn": navn.title(),
        "detalje": detalje,
        "pris": round(aktuel, 2),
        "normalpris": round(normal, 2),
        "rabat_pct": round((1 - aktuel / normal) * 100),
        "pris_pr_enhed": pris.get("price_per_unit") or "",
        "maks_antal": pris.get("max_quantity") or 0,
        "gyldig_til": pris.get("price_changes_on"),
        "afdeling": afd.get("name", ""),
        "kategori": kat.get("name", ""),
        "maerker": vare.get("labels") or [],
        # Deklarationen indeholder allergener. Kortet ned, ellers fylder
        # den for meget i prompten.
        "deklaration": _rens(vare.get("declaration") or "")[:200],
    }


def _rens(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


def til_prompt_linjer(tilbud: list[dict], maks: int = 140) -> str:
    """Kompakt tekstrepræsentation — sparer tokens uden at tabe det vigtige."""
    linjer = []
    for t in tilbud[:maks]:
        maks_txt = ""
        if t["maks_antal"]:
            maks_txt = ", maks " + str(t["maks_antal"]) + " stk."
        linjer.append(
            "[{id}] {navn} ({detalje}) — {pris:.2f} kr. før {normal:.2f} "
            "(−{rabat}%), {enhed}{maks} · {afd}".format(
                id=t["id"],
                navn=t["navn"],
                detalje=t["detalje"],
                pris=t["pris"],
                normal=t["normalpris"],
                rabat=t["rabat_pct"],
                enhed=t["pris_pr_enhed"],
                maks=maks_txt,
                afd=t["afdeling"],
            )
        )
    return "\n".join(linjer)
