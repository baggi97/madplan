"""Næringsberegning ud fra DTU's Frida-database.

Modellen normaliserer sproget — `raavarer` fra kald 2 er rene substantiver med
vægt i gram — og her slås de op i en tabel. Den arbejdsdeling er hele
designet: at parse "4 danske koteletter (ca. 600 g)" ud af prosaen gav
nonsens-tal, fordi netop proteinkilden missede.

**Vi viser hellere ingenting end et forkert tal.** Misser en råvare der vejer
mere end `MIN_VAESENTLIG_GRAM`, er hele retten upålidelig, og `beregn()`
melder `sikker=False`. En kotelet-ret der lander på 7 g protein er værre end
ingen oplysning.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

TABEL = Path(__file__).resolve().parent / "frida.json"

# Hvor sikkert et fuzzy-match skal være. Under det melder vi pas.
GRAENSE = 0.80

# En manglende råvare tungere end dette gør hele rettens tal upålideligt.
# Krydderier og en klat smør flytter ingenting; 600 g kød gør.
MIN_VAESENTLIG_GRAM = 150


def _indlaes() -> tuple[list[dict], dict]:
    if not TABEL.exists():
        log.warning("%s findes ikke — næringstal slås fra", TABEL.name)
        return [], {}
    data = json.loads(TABEL.read_text(encoding="utf-8"))
    return data.get("varer") or [], data.get("alias") or {}


VARER, ALIAS = _indlaes()
_EFTER_NAVN = {v["navn"]: v for v in VARER}
# (basisnavn, fuldt navn, vare) — basisnavnet er alt før første komma, altså
# selve råvaren uden tilstand: "Gulerod" af "Gulerod, dansk, rå".
_OPSLAG = [
    (re.sub(r"[^a-zæøå]", "", v["navn"].split(",")[0].lower()),
     re.sub(r"[^a-zæøå]", "", v["navn"].lower()),
     v)
    for v in VARER
]


def _noegle(s: str) -> str:
    return re.sub(r"[^a-zæøå]", "", str(s).lower())


def slaa_op(raavare: str) -> tuple[dict | None, float]:
    """Finder råvaren i Frida. Returnerer (vare, sikkerhed)."""
    tekst = " ".join(str(raavare).lower().split())
    if tekst in ALIAS:
        vare = _EFTER_NAVN.get(ALIAS[tekst])
        if vare:
            return vare, 1.0

    k = _noegle(tekst)
    if not k:
        return None, 0.0

    bedst, bedste_score = None, 0.0
    for basis, fuld, vare in _OPSLAG:
        score = max(
            difflib.SequenceMatcher(None, k, basis).ratio(),
            difflib.SequenceMatcher(None, k, fuld).ratio() * 0.95,
        )
        # Præfiks tæller kun når ordene er nogenlunde lige lange. Uden det
        # bliver 'mel' til 'melbanan' og 'mælk' til 'mælkebøtte' — med 0,90
        # i sikkerhed, hvilket er værre end at melde pas.
        if basis and (k.startswith(basis) or basis.startswith(k)):
            if min(len(k), len(basis)) / max(len(k), len(basis)) >= 0.7:
                score = max(score, 0.92)
        if score > bedste_score:
            bedst, bedste_score = vare, score

    return (bedst, bedste_score) if bedste_score >= GRAENSE else (None, bedste_score)


def beregn(raavarer: list | None, portioner: int) -> dict | None:
    """Protein og kalorier pr. portion. `None` hvis der ikke er data nok.

    `sikker` er falsk hvis en væsentlig råvare ikke kunne slås op — så skal
    tallet ikke vises.
    """
    if not raavarer or not portioner:
        return None

    protein = kalorier = 0.0
    manglende = []
    for post in raavarer:
        if not isinstance(post, dict):
            continue
        try:
            gram = float(post.get("gram") or 0)
        except (TypeError, ValueError):
            gram = 0.0
        navn = str(post.get("raavare") or "")
        if gram <= 0 or not navn:
            continue

        vare, score = slaa_op(navn)
        if not vare:
            log.info("Ingen næringsdata for '%s' (%.0f g, bedste match %.2f)",
                     navn, gram, score)
            if gram >= MIN_VAESENTLIG_GRAM:
                manglende.append(navn)
            continue
        protein += vare["protein"] * gram / 100
        kalorier += vare["kcal"] * gram / 100

    if protein == 0 and kalorier == 0:
        return None
    return {
        "protein_g": round(protein / portioner),
        "kalorier": round(kalorier / portioner),
        "sikker": not manglende,
        "manglende": manglende,
    }
