"""Fælles opsætning. Ingen test må røre rigtige data eller netværket."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402


@pytest.fixture(autouse=True)
def _isoleret_data(tmp_path, monkeypatch):
    """Peger DATA_DIR på en tom mappe pr. test.

    Uden det ville testene læse og skrive i den rigtige data/-mappe, og en
    test af historikken ville pludselig afhænge af hvad familien spiste
    sidste uge.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return tmp_path


@pytest.fixture
def praef():
    """Et realistisk udsnit af praeferencer.yaml."""
    return {
        "standard_portioner": 4,
        "allergier": ["nødder"],
        "kan_vi_ikke_lide": ["lever", "selleri"],
        "har_altid_hjemme": ["ris", "pasta", "løg", "mel", "bouillonterninger"],
        "kostregler": {
            "profil": ["proteinrige retter"],
            "maks_vegetar": 1,
            "maks_fisk": 1,
            "maks_asiatisk": 1,
            "maks_dyre": 1,
            "dyr_over_kr": 45,
            "maks_gentaget_tilbud": 2,
        },
    }


@pytest.fixture
def tilbud():
    return [
        {"id": "1", "navn": "Hakket Oksekød", "detalje": "500 g", "pris": 25.0,
         "normalpris": 35.0, "rabat_pct": 29, "pris_pr_enhed": "50.00 per Kg.",
         "maks_antal": 0, "gyldig_til": None, "afdeling": "Køl", "kategori": "Kød"},
        {"id": "2", "navn": "Bladselleri", "detalje": "1 bdt", "pris": 8.0,
         "normalpris": 12.0, "rabat_pct": 33, "pris_pr_enhed": "", "maks_antal": 0,
         "gyldig_til": None, "afdeling": "Frugt & grønt", "kategori": "Grønt"},
        {"id": "3", "navn": "Spidskål", "detalje": "1 stk", "pris": 10.0,
         "normalpris": 15.0, "rabat_pct": 33, "pris_pr_enhed": "", "maks_antal": 0,
         "gyldig_til": None, "afdeling": "Frugt & grønt", "kategori": "Grønt"},
        {"id": "4", "navn": "Kyllingebryst", "detalje": "700 g", "pris": 49.0,
         "normalpris": 65.0, "rabat_pct": 25, "pris_pr_enhed": "70.00 per Kg.",
         "maks_antal": 2, "gyldig_til": "2026-09-05", "afdeling": "Køl",
         "kategori": "Fjerkræ"},
    ]


def ret(navn="Ret", ids=None, kategori="koed", koekken="dansk", pris=25, **ekstra):
    """Et minimalt gyldigt forslag. Testene overskriver det de bryder sig om."""
    return {
        "navn": navn,
        "beskrivelse": "En helt almindelig ret.",
        "tid_min": 25,
        "pris_pr_portion": pris,
        "tilbuds_ids": [] if ids is None else ids,
        "kategori": kategori,
        "koekken": koekken,
        **ekstra,
    }
