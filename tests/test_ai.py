"""Filtrene i ai.py.

Hver af dem koder en fejl vi har ramt i produktion. Kommentarerne siger hvilken,
så det er tydeligt hvad testen værner om hvis nogen får lyst til at forenkle.
"""
from __future__ import annotations

import datetime
import json
import unittest.mock as mock

import pytest

from app import ai, config, store
from tests.conftest import ret


# --- _udpak_retter ----------------------------------------------------
# Modellen dobbeltkodede hele svaret som en JSON-streng. Uden udpakningen
# løb valideringen hen over strengen tegn for tegn og kasserede alt.

@pytest.mark.parametrize(
    "svar, forventet",
    [
        ({"retter": [{"navn": "A"}]}, 1),
        ({"retter": json.dumps({"retter": [{"navn": "A"}]})}, 1),
        ({"retter": json.dumps([{"navn": "A"}])}, 1),
        ({"retter": "bare tekst der ikke er JSON"}, 0),
        ({"retter": 42}, 0),
        ({}, 0),
        ("hele svaret er en streng", 0),
    ],
)
def test_udpak_retter(svar, forventet):
    assert len(ai._udpak_retter(svar)) == forventet


# --- _valider_forslag -------------------------------------------------

def test_valider_kasserer_ikke_objekter():
    # Modellen har returneret en ren streng midt i listen
    assert ai._valider_forslag(["en streng", None, ret()], {"1"}) == [ret()]


def test_valider_kraever_felter():
    assert ai._valider_forslag([ret(navn="")], {"1"}) == []
    assert ai._valider_forslag([{**ret(), "tid_min": None}], {"1"}) == []


def test_valider_pris_nul_er_lovligt():
    assert len(ai._valider_forslag([ret(pris=0)], {"1"})) == 1


def test_valider_kasserer_opdigtede_id():
    assert ai._valider_forslag([ret(ids=["999"])], {"1"}) == []


def test_valider_tillader_ret_uden_tilbud():
    # Sæsonretter har tom liste — det må ikke kasseres
    assert len(ai._valider_forslag([ret(ids=[])], {"1"})) == 1


def test_valider_kasserer_tilbuds_ids_der_ikke_er_liste():
    assert ai._valider_forslag([{**ret(), "tilbuds_ids": "1,2"}], {"1"}) == []


# --- _haandhaev_lofter ------------------------------------------------
# Modellen overholder ikke "højst én" pålideligt.

def test_lofter_paa_tvaers_af_akser(praef):
    regler = praef["kostregler"]
    ind = [
        ret("Thai", ids=["1"], koekken="asiatisk"),
        ret("Ramen", ids=["1"], koekken="asiatisk"),      # over asiatisk-loft
        ret("Rib-eye", ids=["1"], pris=95),
        ret("Højreb", ids=["1"], pris=75),                # over dyre-loft
        ret("Torsk", ids=["1"], kategori="fisk"),
        ret("Laks", ids=["1"], kategori="fisk"),          # over fisk-loft
        ret("Frikadeller", ids=["1"]),
    ]
    ud = ai._haandhaev_lofter(ind, regler)
    assert [r["navn"] for r in ud] == ["Thai", "Rib-eye", "Torsk", "Frikadeller"]


def test_dyre_graense_er_skarp(praef):
    regler = praef["kostregler"]
    ud = ai._haandhaev_lofter([ret("Lige paa", pris=45), ret("Lige over", pris=45.01)], regler)
    assert [r["navn"] for r in ud] == ["Lige paa", "Lige over"]


def test_uden_regler_trimmes_intet():
    ind = [ret("A", kategori="fisk"), ret("B", kategori="fisk")]
    assert ai._haandhaev_lofter(ind, {}) == ind


def test_ukendt_kategori_slipper_igennem(praef):
    assert len(ai._haandhaev_lofter([ret(kategori="dessert")], praef["kostregler"])) == 1


# --- _spred_tilbud ----------------------------------------------------
# Spidskål i tre af ti aftener er billigt, men ensformigt.

def test_spred_tilbud():
    ind = [ret("A", ids=["k"]), ret("B", ids=["k"]), ret("C", ids=["k"]), ret("D", ids=["x"])]
    assert [r["navn"] for r in ai._spred_tilbud(ind, 2)] == ["A", "B", "D"]


def test_spred_tilbud_uden_loft():
    ind = [ret("A", ids=["k"]), ret("B", ids=["k"]), ret("C", ids=["k"])]
    assert ai._spred_tilbud(ind, None) == ind


# --- _fjern_uoenskede -------------------------------------------------
# Bladselleri stod på fravalgslisten og kom alligevel to uger i træk.

def test_fravalg_rammer_varenavn(praef, tilbud):
    ind = [ret("Gryde", ids=["2"]), ret("Anden", ids=["1"])]
    assert [r["navn"] for r in ai._fjern_uoenskede(ind, tilbud, praef)] == ["Anden"]


def test_fravalg_rammer_retnavn_og_beskrivelse(praef, tilbud):
    assert ai._fjern_uoenskede([ret("Sellerisalat")], tilbud, praef) == []
    besk = {**ret("Gryde"), "beskrivelse": "Med masser af knoldselleri."}
    assert ai._fjern_uoenskede([besk], tilbud, praef) == []


def test_fravalg_matcher_delstreng(praef, tilbud):
    # 'selleri' skal også fange 'bladselleri', og 'lever' fange 'leverpostej'
    assert ai._fjern_uoenskede([ret("Leverpostejmad")], tilbud, praef) == []


def test_fravalg_uden_liste_trimmer_intet(tilbud):
    ind = [ret("Sellerisalat")]
    assert ai._fjern_uoenskede(ind, tilbud, {}) == ind


# --- _fjern_gentagelser -----------------------------------------------

@pytest.mark.parametrize(
    "navn, beholdes",
    [
        ("Kyllingegryde med champignon", False),   # identisk
        ("KYLLINGEGRYDE MED CHAMPIGNON!", False),  # kun tegn og store bogstaver
        ("Kyllingegryde med svampe", False),       # omskrivning
        ("Tunsteak med spidskål", True),           # helt anden ret
        ("Lasagne", True),
    ],
)
def test_fjern_gentagelser(navn, beholdes):
    tidligere = ["Kyllingegryde med champignon"]
    assert bool(ai._fjern_gentagelser([ret(navn)], tidligere)) is beholdes


def test_tom_historik_lader_alt_passere():
    ind = [ret("A"), ret("B")]
    assert ai._fjern_gentagelser(ind, []) == ind


# --- _maks_uden_tilbud ------------------------------------------------

def test_maks_uden_tilbud_bevarer_raekkefoelge():
    ind = [ret("m1", ids=["1"]), ret("u1"), ret("m2", ids=["2"]), ret("u2"), ret("u3")]
    assert [r["navn"] for r in ai._maks_uden_tilbud(ind, 2)] == ["m1", "u1", "m2", "u2"]


def test_maks_uden_tilbud_nul_kraever_tilbud_paa_alle():
    ind = [ret("m", ids=["1"]), ret("u")]
    assert [r["navn"] for r in ai._maks_uden_tilbud(ind, 0)] == ["m"]


# --- basisvarer -------------------------------------------------------
# Prompten bad om det, men 3 af 19 varer slap alligevel igennem.

@pytest.mark.parametrize(
    "vare, rammes",
    [
        ("2 løg", True), ("500 g pastaskruer", True), ("1 dl ris", True),
        ("2 spsk mel", True), ("1 bouillonterning", True), ("2 bouillonterninger", True),
        ("1 melon", False),               # 'mel' må ikke fange 'melon'
        ("1 bundt frisk persille", False),  # 'ris' må ikke fange 'frisk'
        ("400 g hakket gris", False),
        ("500 g kartofler", False),
    ],
)
def test_basisvaremoenster(praef, vare, rammes):
    m = ai._basisvaremoenster(praef["har_altid_hjemme"])
    assert bool(m.search(vare)) is rammes


def test_fjern_basisvarer_tommer_afdelinger_ud(praef):
    madplan = {
        "opskrifter": [],
        "indkoebsliste": [
            {"afdeling": "Kolonial", "varer": [{"vare": "1 dl ris", "paa_tilbud": False}]},
            {"afdeling": "Køl", "varer": [{"vare": "500 g kylling", "paa_tilbud": True}]},
        ],
    }
    ud = ai._fjern_basisvarer(madplan, praef)
    assert [g["afdeling"] for g in ud["indkoebsliste"]] == ["Køl"]


# --- _saeson ----------------------------------------------------------

@pytest.mark.parametrize(
    "maaned, aarstid",
    [(1, "vinter"), (3, "forår"), (6, "sommer"), (9, "efterår"), (12, "vinter")],
)
def test_saeson(maaned, aarstid):
    with mock.patch.object(store, "nu", return_value=datetime.datetime(2026, maaned, 15)):
        assert ai._saeson()[1] == aarstid


# --- _regeltekst ------------------------------------------------------

def test_regeltekst_er_generisk_over_maks(praef):
    tekst = ai._regeltekst(praef["kostregler"])
    assert "proteinrige retter" in tekst
    assert "'fisk'" in tekst and "'asiatisk'" in tekst
    assert "45 kr. pr. portion" in tekst


def test_regeltekst_tom_uden_regler():
    assert ai._regeltekst({}) == ""


def test_ny_maks_regel_virker_uden_kodeaendring():
    # Håndhævelsen skal være generisk over maks_*
    regler = {"maks_mexicansk": 1}
    ind = [ret("A", koekken="mexicansk"), ret("B", koekken="mexicansk")]
    assert [r["navn"] for r in ai._haandhaev_lofter(ind, regler)] == ["A"]
