"""Konfiguration læst fra miljøvariabler og praeferencer.yaml."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE / "data"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", BASE / "config"))

TZ = os.getenv("TZ", "Europe/Copenhagen")

# --- Web --------------------------------------------------------------
WEB_PORT = int(os.getenv("WEB_PORT", "8099"))

# --- Tilbudskilde -----------------------------------------------------
# Det katalog-API som REMA-appen selv bruger. Kræver ingen nøgle.
REMA_STORE_ID = os.getenv("REMA_STORE_ID", "1")
REMA_URL = (
    "https://api.digital.rema1000.dk/api/v1/catalog/store/"
    + REMA_STORE_ID
    + "/departments"
)

# 10 Brød, 20 Frugt & grønt, 30 Kød/fisk/fjerkræ, 40 Køl, 50 Frost,
# 60 Mejeri, 70 Ost, 80 Kolonial, 160 Nemt & hurtigt
MAD_AFDELINGER = {
    int(x)
    for x in os.getenv("MAD_AFDELINGER", "10,20,30,40,50,60,70,80,160").split(",")
}

# --- Anthropic --------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# --- Push-beskeder (valgfrit) -----------------------------------------
# Lav et nøglepar med:  python -m app.push
# Kræver HTTPS på websitet — se README. Tomme nøgler slår push fra.
VAPID_OFFENTLIG_NOEGLE = os.getenv("VAPID_OFFENTLIG_NOEGLE", "")
VAPID_PRIVAT_NOEGLE = os.getenv("VAPID_PRIVAT_NOEGLE", "")
# Push-tjenesten kræver en kontaktadresse den kan skrive til ved problemer
VAPID_KONTAKT = os.getenv("VAPID_KONTAKT", "mailto:madplan@eksempel.dk")

# --- Tidsplan ("ugedag time", lokal tid) ------------------------------
FORSLAG_CRON = os.getenv("FORSLAG_CRON", "sun 8")
DEADLINE_CRON = os.getenv("DEADLINE_CRON", "sun 18")
# Puf til familien hvis ingen har valgt endnu. Skal ligge før deadline, så der
# er tid til at nå det.
PAAMINDELSE_CRON = os.getenv("PAAMINDELSE_CRON", "sun 17")

ANTAL_FORSLAG = int(os.getenv("ANTAL_FORSLAG", "15"))
ANTAL_RETTER = int(os.getenv("ANTAL_RETTER", "5"))  # fallback hvis ingen stemmer

# Hvor mange af forslagene der skal bygge på ugens tilbud. Resten må være
# almindelige sæsonretter — der er sjældent 15 fornuftige retter i én uges
# tilbud, og så bliver de sidste til fyld hvis man tvinger dem igennem.
MIN_MED_TILBUD = int(os.getenv("MIN_MED_TILBUD", "8"))

# Retter serveret inden for så mange uger foreslås ikke igen
UNDGAA_UGER = int(os.getenv("UNDGAA_UGER", "4"))
# Hvor ens to retnavne må være før den nye regnes for en gentagelse.
# Heuristik — hver frasortering logges, så tallet kan kalibreres.
GENTAGELSE_GRAENSE = float(os.getenv("GENTAGELSE_GRAENSE", "0.72"))

# Færre tilbud end dette betyder at kilden sandsynligvis er brudt
MIN_TILBUD = int(os.getenv("MIN_TILBUD", "20"))


def hent_praeferencer() -> dict:
    sti = CONFIG_DIR / "praeferencer.yaml"
    if not sti.exists():
        return {}
    return yaml.safe_load(sti.read_text(encoding="utf-8")) or {}


def valider() -> list[str]:
    """Returnerer en liste af manglende, påkrævede indstillinger."""
    return [] if ANTHROPIC_API_KEY else ["ANTHROPIC_API_KEY"]
