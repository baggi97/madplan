"""Normalisering og frasortering af tilbud. Ingen netværkskald."""
from __future__ import annotations

import pytest

from app import rema


def vare(navn, pris=10.0, normal=20.0, tilbud=True, underline="", **ekstra):
    return {
        "id": 42,
        "name": navn,
        "underline": underline,
        "pricing": {
            "is_on_discount": tilbud,
            "normal_price": normal,
            "price": pris,
            "price_per_unit": "20.00 per Kg.",
            "max_quantity": 0,
            "price_changes_on": "2026-09-05",
        },
        **ekstra,
    }


AFD = {"id": 30, "name": "Kød"}
KAT = {"name": "Hakket kød"}


def test_normaliser_felter():
    ud = rema._normaliser(vare("HAKKET OKSEKØD", pris=25.0, normal=35.0), AFD, KAT)
    assert ud["navn"] == "Hakket Oksekød"      # title-case
    assert ud["pris"] == 25.0 and ud["normalpris"] == 35.0
    assert ud["rabat_pct"] == 29
    assert ud["id"] == "42"                     # altid streng
    assert ud["afdeling"] == "Kød"


def test_ikke_paa_tilbud_frasorteres():
    assert rema._normaliser(vare("Kød", tilbud=False), AFD, KAT) is None


def test_annonceret_men_ikke_billigere_frasorteres():
    # is_advertised er ikke det samme som på tilbud — vi kræver price < normal
    assert rema._normaliser(vare("Kød", pris=20.0, normal=20.0), AFD, KAT) is None
    assert rema._normaliser(vare("Kød", pris=25.0, normal=20.0), AFD, KAT) is None


def test_deklaration_strimles_og_beskaeres():
    ud = rema._normaliser(
        vare("Kød", declaration="<p>Indeholder <b>hvede</b></p>" + "x" * 500), AFD, KAT
    )
    assert "<" not in ud["deklaration"]
    assert len(ud["deklaration"]) <= 200


@pytest.mark.parametrize(
    "navn, frasorteres",
    [
        ("Sodavand", True), ("Lakrids", True), ("Hundefoder", True),
        ("Vaniljeis", False),          # 'is' kun som helt ord
        ("Is", True),
        ("Hakket Oksekød", False), ("Spidskål", False),
    ],
)
def test_udeluk(navn, frasorteres):
    assert bool(rema.UDELUK.search(navn)) is frasorteres


@pytest.mark.parametrize(
    "navn, frasorteres",
    [
        ("Gullaschsuppe", True), ("Tomatsuppe", True), ("Kartoffel-Porre Suppe", True),
        ("Suppehøne", False),          # ingrediens til rigtig mad
        ("Suppeurter", False),
        ("Kødboller", False),
    ],
)
def test_udeluk_navn_kun_paa_varenavnet(navn, frasorteres):
    assert bool(rema.UDELUK_NAVN.search(navn)) is frasorteres


def test_suppe_i_kategorien_frasorterer_ikke_ingrediensen():
    # REMA's suppe-kategori rummer også suppeurter — de skal overleve
    assert rema._normaliser(vare("SUPPEURTER"), AFD, {"name": "Suppe"}) is not None


def test_til_prompt_linjer_beskaerer(tilbud):
    linjer = rema.til_prompt_linjer(tilbud, maks=2).splitlines()
    assert len(linjer) == 2
    assert linjer[0].startswith("[1] Hakket Oksekød")


def test_til_prompt_linjer_viser_maks_antal(tilbud):
    tekst = rema.til_prompt_linjer(tilbud)
    assert "maks 2 stk." in tekst        # kyllingebrystet har max_quantity 2
