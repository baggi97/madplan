"""Næringsberegning mod Frida.

Testene her er skrevet ud fra de konkrete fejl undersøgelsen afslørede. En
matcher der tager konfident fejl er værre end en der melder pas: første forsøg
gav 'mel' -> 'Melbanan' og 'mælk' -> 'Mælkebøtte' med 0,90 i sikkerhed.
"""
from __future__ import annotations

import json

import pytest

from app import naering


def test_tabellen_er_med_i_repoet():
    assert naering.TABEL.exists(), "kør vaerktoej/lav_frida_tabel.py"
    assert len(naering.VARER) > 1000
    assert naering.ALIAS


def test_alle_alias_findes_i_tabellen():
    """Et forkert aliasnavn falder tilbage på fuzzy og rådner tavst.
    Værktøjet validerer ved bygning; her fanges det hvis nogen retter i hånden."""
    navne = {v["navn"] for v in naering.VARER}
    ukendte = {k: v for k, v in naering.ALIAS.items() if v not in navne}
    assert not ukendte, "aliasnavne uden for tabellen: %s" % ukendte


@pytest.mark.parametrize(
    "raavare, forventet",
    [
        ("mel", "Hvedemel"),
        ("mælk", "Letmælk, konventionel (ikke-økologisk)"),
        ("fløde", "Fløde 18 %"),
        ("kartofler", "Kartoffel, uspec., rå"),
        ("svinekotelet", "Grisekød, nakkefilet, helt afpudset (Nakkekotelet), rå"),
        ("hakket oksekød", "Oksekød, hakket, 8-12% fedt, rå"),
    ],
)
def test_alias_slaar_igennem(raavare, forventet):
    vare, sikkerhed = naering.slaa_op(raavare)
    assert vare and vare["navn"] == forventet
    assert sikkerhed == 1.0


@pytest.mark.parametrize("raavare, forbudt", [("mel", "banan"), ("mælk", "bøtte")])
def test_kort_ord_rammer_ikke_langt_sammensat(raavare, forbudt):
    """'mel' må ikke blive til 'melbanan'. Præfiks tæller kun når ordene er
    nogenlunde lige lange."""
    vare, _ = naering.slaa_op(raavare)
    assert vare and forbudt not in vare["navn"].lower()


def test_fuzzy_virker_uden_alias():
    vare, sikkerhed = naering.slaa_op("broccoli")
    assert vare and "Broccoli" in vare["navn"] and sikkerhed >= naering.GRAENSE


@pytest.mark.parametrize("raavare", ["kyllingespyd med bbq", "indbagt laks med spinat", "xyzzy"])
def test_ukendt_raavare_melder_pas(raavare):
    vare, _ = naering.slaa_op(raavare)
    assert vare is None


# --- beregn -----------------------------------------------------------

def test_beregner_pr_portion():
    ud = naering.beregn([{"raavare": "hakket oksekød", "gram": 800}], portioner=4)
    # 8-12% hakket okse: ca. 20 g protein pr. 100 g -> 200 g fordelt på fire
    assert 35 <= ud["protein_g"] <= 45
    assert ud["sikker"] is True


def test_manglende_vaesentlig_raavare_goer_tallet_usikkert():
    ud = naering.beregn(
        [{"raavare": "kartofler", "gram": 500},
         {"raavare": "kyllingespyd med bbq", "gram": 600}],
        portioner=4,
    )
    assert ud["sikker"] is False
    assert "kyllingespyd med bbq" in ud["manglende"]


def test_lille_manglende_raavare_er_uden_betydning():
    ud = naering.beregn(
        [{"raavare": "hakket oksekød", "gram": 600},
         {"raavare": "xyzzy", "gram": 5}],
        portioner=4,
    )
    assert ud["sikker"] is True


@pytest.mark.parametrize("raavarer", [None, [], [{"raavare": "xyzzy", "gram": 10}]])
def test_intet_at_regne_paa_giver_intet_tal(raavarer):
    assert naering.beregn(raavarer, portioner=4) is None


def test_nul_portioner_giver_intet_tal():
    assert naering.beregn([{"raavare": "mel", "gram": 100}], portioner=0) is None


def test_taaler_vrøvl_i_listen():
    ud = naering.beregn(
        ["en streng", {"raavare": "mel"}, {"gram": 100},
         {"raavare": "hakket oksekød", "gram": 400}],
        portioner=2,
    )
    assert ud and ud["protein_g"] > 0


# --- gylden prøve -----------------------------------------------------

def test_gylden_madplan():
    """Den madplan undersøgelsen brugte. Kalvekødsretten skal lande plausibelt;
    de retter hvor proteinkilden ikke kan slås op, skal melde usikkert frem for
    at vise 7 g."""
    kalvekoed = naering.beregn(
        [{"raavare": "kalvekød", "gram": 600},
         {"raavare": "broccoli", "gram": 800},
         {"raavare": "ris", "gram": 300},
         {"raavare": "løg", "gram": 110}],
        portioner=4,
    )
    assert kalvekoed["sikker"] is True
    assert 40 <= kalvekoed["protein_g"] <= 60

    laks = naering.beregn(
        [{"raavare": "indbagt laks med spinat", "gram": 1000},
         {"raavare": "bulgur", "gram": 300}],
        portioner=4,
    )
    assert laks["sikker"] is False   # hellere "—" end 13 g
