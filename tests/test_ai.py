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


# --- _fordel_tilbud ---------------------------------------------------
# Loft i begge retninger. Uden et loft paa tilbudssiden bygger modellen alle
# retter paa tilbud, og saa forsvinder saesonretterne — maalt 2026-09-03.

def test_fordeling_bevarer_raekkefoelge():
    ind = [ret("m1", ids=["1"]), ret("u1"), ret("m2", ids=["2"]), ret("u2"), ret("u3")]
    assert [r["navn"] for r in ai._fordel_tilbud(ind, 2, 2)] == ["m1", "u1", "m2", "u2"]


def test_for_mange_med_tilbud_trimmes():
    ind = [ret("m%d" % i, ids=["1"]) for i in range(4)] + [ret("u1")]
    assert [r["navn"] for r in ai._fordel_tilbud(ind, 2, 2)] == ["m0", "m1", "u1"]


def test_nul_uden_tilbud_kraever_tilbud_paa_alle():
    ind = [ret("m", ids=["1"]), ret("u")]
    assert [r["navn"] for r in ai._fordel_tilbud(ind, 5, 0)] == ["m"]


def test_negativt_loft_slaar_fra():
    ind = [ret("m", ids=["1"]), ret("u")]
    assert ai._fordel_tilbud(ind, -1, -1) == ind


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


# --- rester i skemaet -------------------------------------------------

def test_rester_er_valgfrit_i_skemaet():
    """Uger gemt før feltet fandtes skal stadig kunne læses."""
    skema = ai.VAERKTOEJ_MADPLAN["input_schema"]
    assert "rester" in skema["properties"]
    assert "rester" not in skema["required"]


def test_dagen_kommer_med_i_prompten_til_kald_2():
    """Uden dagen kan resterne ikke pege på 'torsdagens ret'."""
    import asyncio
    fanget = {}

    async def falsk_kald(system, besked, vaerktoej, maks):
        fanget["system"] = system
        fanget["besked"] = besked
        return {"opskrifter": [], "indkoebsliste": []}

    oprindelig = ai._kald
    ai._kald = falsk_kald
    try:
        asyncio.run(ai.lav_madplan(
            [{"navn": "Karry", "portioner": 3, "dag": "torsdag", "tilbuds_ids": []}],
            [], {"standard_portioner": 4},
        ))
    finally:
        ai._kald = oprindelig

    assert "til 3 personer (torsdag)" in fanget["besked"]
    assert "rester" in fanget["system"].lower()


# --- næringskrav ------------------------------------------------------
# Tallene er modellens skøn, ikke en beregning. Filteret afviser dem
# modellen selv indrømmer er for lave.

NAERING = {"naering": {"min_protein_g": 50, "maks_kalorier": 700}}


def _n(navn, protein=None, kalorier=None):
    r = ret(navn)
    if protein is not None:
        r["protein_g"] = protein
    if kalorier is not None:
        r["kalorier"] = kalorier
    return r


@pytest.mark.parametrize(
    "protein, kalorier, beholdes",
    [
        (55, 650, True),      # opfylder begge
        (50, 700, True),      # præcis på grænserne
        (49, 650, False),     # for lidt protein
        (55, 701, False),     # for mange kalorier
        (30, 900, False),     # begge dele
    ],
)
def test_naeringskrav(protein, kalorier, beholdes):
    ud = ai._naeringskrav([_n("Ret", protein, kalorier)], NAERING)
    assert bool(ud) is beholdes


def test_manglende_tal_kasseres():
    """Ellers ville 'glem at udfylde feltet' være vejen udenom kravet."""
    assert ai._naeringskrav([_n("Uden protein", kalorier=600)], NAERING) == []
    assert ai._naeringskrav([_n("Uden kcal", protein=60)], NAERING) == []
    assert ai._naeringskrav([_n("Uden begge")], NAERING) == []


def test_uden_naeringsregler_trimmes_intet():
    ind = [_n("A"), _n("B", 10, 2000)]
    assert ai._naeringskrav(ind, {}) == ind
    assert ai._naeringskrav(ind, {"naering": {}}) == ind


def test_kun_proteinkrav():
    regler = {"naering": {"min_protein_g": 50}}
    assert len(ai._naeringskrav([_n("A", 60, 2000)], regler)) == 1   # kcal ligegyldig
    assert ai._naeringskrav([_n("B", 40, 300)], regler) == []


def test_naeringstal_er_paakraevet_i_skemaet():
    felter = ai.VAERKTOEJ_FORSLAG["input_schema"]["properties"]["retter"]["items"]
    assert "protein_g" in felter["properties"] and "kalorier" in felter["properties"]
    assert "protein_g" in felter["required"] and "kalorier" in felter["required"]


def test_gentaget_tilbud_beskrives_rigtigt_i_prompten():
    """Den generiske maks_*-løkke ville ellers skrive 'retter af typen
    gentaget_tilbud', hvilket ikke betyder noget."""
    tekst = ai._regeltekst({"maks_gentaget_tilbud": 2})
    assert "samme tilbud i 2 retter" in tekst
    assert "typen 'gentaget_tilbud'" not in tekst


def test_naeringskrav_staar_i_prompten():
    tekst = ai._regeltekst(NAERING)
    assert "Mindst 50 g protein" in tekst
    assert "Højst 700 kcal" in tekst


# --- nedstemte retter -------------------------------------------------

def test_fjern_nedstemte_bruger_samme_navnematch():
    ned = ["Kyllingegryde med champignon"]
    assert ai._fjern_nedstemte([ret("Kyllingegryde med champignon")], ned) == []
    assert ai._fjern_nedstemte([ret("Kyllingegryde med svampe")], ned) == []
    assert len(ai._fjern_nedstemte([ret("Lasagne")], ned)) == 1


def test_ingen_nedstemte_trimmer_intet():
    ind = [ret("A"), ret("B")]
    assert ai._fjern_nedstemte(ind, []) == ind


# --- kald 2 kan også dobbeltkode ---------------------------------------
# Samme fejl som ramte forslagene, men lav_madplan havde aldrig vaernet.
# Set 2026-09-06: opskrifter indeholdt en streng, og plan-siden væltede.

def _plan(opskrifter, indkoeb=None):
    return {"opskrifter": opskrifter, "indkoebsliste": indkoeb if indkoeb is not None else []}


def test_madplan_dobbeltkodet_som_streng():
    god = {"navn": "Karry", "portioner": 4}
    ud = ai._udpak_madplan(json.dumps(_plan([god]), ensure_ascii=False))
    assert ud["opskrifter"] == [god]


def test_opskrifter_som_streng_pakkes_ud():
    god = {"navn": "Karry", "portioner": 4}
    ud = ai._udpak_madplan({"opskrifter": json.dumps([god]), "indkoebsliste": []})
    assert ud["opskrifter"] == [god]


def test_strenge_i_opskriftslisten_kasseres():
    god = {"navn": "Karry", "portioner": 4}
    ud = ai._udpak_madplan(_plan(["en streng", None, god]))
    assert ud["opskrifter"] == [god]


def test_opskrift_uden_navn_kasseres():
    ud = ai._udpak_madplan(_plan([{"portioner": 4}, {"navn": "Karry", "portioner": 4}]))
    assert [o["navn"] for o in ud["opskrifter"]] == ["Karry"]


@pytest.mark.parametrize("svar", ["ikke json", 42, None, {"opskrifter": 7}])
def test_vroevl_giver_tom_madplan_frem_for_at_vaelte(svar):
    ud = ai._udpak_madplan(svar)
    assert ud["opskrifter"] == [] and ud["indkoebsliste"] == []


def test_oevrige_felter_bevares():
    ud = ai._udpak_madplan({"opskrifter": [], "indkoebsliste": [],
                            "rester": [{"vare": "fløde", "forslag": "brug den"}]})
    assert ud["rester"]
