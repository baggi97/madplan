"""Datalaget. DATA_DIR peger på en tom mappe pr. test — se conftest."""
from __future__ import annotations

import datetime

import pytest
import json

from app import config, store


def test_uge_noegle_bruger_isokalender():
    assert store.uge_noegle(datetime.date(2026, 8, 30)) == "2026-W35"
    assert store.uge_nummer("2026-W05") == "5"


# --- planuge ----------------------------------------------------------
# Fejlen i drift søndag 2026-09-06: køringen kl. 8 skrev til den uge der lige
# var gået, fandt de forslag der allerede lå der, og meldte "forslag findes
# allerede" i stedet for at planlægge den kommende uge.

@pytest.mark.parametrize(
    "dato, ugedag, iso, plan",
    [
        ("2026-09-07", "mandag",  "2026-W37", "2026-W37"),
        ("2026-09-09", "onsdag",  "2026-W37", "2026-W37"),
        ("2026-09-12", "lørdag",  "2026-W37", "2026-W37"),
        ("2026-09-06", "søndag",  "2026-W36", "2026-W37"),   # ruller frem
        ("2026-09-13", "søndag",  "2026-W37", "2026-W38"),
    ],
)
def test_planuge_ruller_frem_om_soendagen(dato, ugedag, iso, plan):
    d = datetime.date.fromisoformat(dato)
    assert store.uge_noegle(d) == iso, "ISO-ugen skal være urørt"
    assert store.planuge(d) == plan, "%s skal planlægge %s" % (ugedag, plan)


def test_planuge_krydser_aarsskifte():
    # Søndag 2026-12-27 er i uge 52; planen gælder uge 53
    assert store.planuge(datetime.date(2026, 12, 27)) == "2026-W53"


def test_hent_uge_bruger_planugen_som_standard(monkeypatch):
    monkeypatch.setattr(store, "planuge", lambda d=None: "2026-W37")
    assert store.hent_uge()["uge"] == "2026-W37"


def test_tom_uge_har_alle_felter():
    u = store.tom_uge("2026-W35")
    for felt in ("status", "tilbud", "forslag", "valgt", "egne", "madplan", "afkrydset"):
        assert felt in u
    assert u["status"] == store.TOM


def test_hent_uge_udfylder_manglende_felter():
    """Gemte uger fra før et nyt felt blev tilføjet skal stadig kunne læses."""
    store._skriv("uge-2026-W35.json", {"uge": "2026-W35", "status": "vaelger"})
    u = store.hent_uge("2026-W35")
    assert u["egne"] == [] and u["forslag"] == []


def test_skrivning_er_atomisk():
    store.gem_uge({**store.tom_uge("2026-W35"), "forslag": [{"navn": "A"}]})
    sti = config.DATA_DIR / "uge-2026-W35.json"
    assert sti.exists()
    assert not list(config.DATA_DIR.glob("*.tmp"))   # ingen halve filer efterladt
    assert json.loads(sti.read_text(encoding="utf-8"))["forslag"] == [{"navn": "A"}]


def test_ulaeselig_fil_giver_standard_i_stedet_for_at_vaelte():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "uge-2026-W35.json").write_text("{ ikke json", encoding="utf-8")
    assert store.hent_uge("2026-W35")["status"] == store.TOM


def test_alle_uger_sorteres_nyeste_foerst():
    for n in ("2026-W33", "2026-W35", "2026-W34"):
        store.gem_uge(store.tom_uge(n))
    assert store.alle_uger() == ["2026-W35", "2026-W34", "2026-W33"]


# --- historik ---------------------------------------------------------

def test_historik_deler_valgt_og_fravalgt():
    forslag = [{"navn": "A"}, {"navn": "B"}, {"navn": "C"}]
    store.tilfoej_historik("2026-W35", forslag, [0, 2])
    h = store.hent_historik()[-1]
    assert h["valgt"] == ["A", "C"] and h["fravalgt"] == ["B"]


def test_egne_retter_taeller_som_valgt():
    egne = [{"navn": "Tarteletter", "valgt": True}, {"navn": "Pizza", "valgt": False}]
    store.tilfoej_historik("2026-W35", [{"navn": "A"}], [0], egne)
    assert store.hent_historik()[-1]["valgt"] == ["A", "Tarteletter"]


def test_samme_uge_overskrives_ikke_dubleres():
    store.tilfoej_historik("2026-W35", [{"navn": "A"}], [0])
    store.tilfoej_historik("2026-W35", [{"navn": "B"}], [0])
    assert len(store.hent_historik()) == 1
    assert store.hent_historik()[0]["valgt"] == ["B"]


def test_seneste_retter_respekterer_vinduet():
    for i, u in enumerate(["2026-W31", "2026-W32", "2026-W33", "2026-W34"]):
        store.tilfoej_historik(u, [{"navn": "Ret%d" % i}], [0])
    assert store.seneste_retter(2) == ["Ret2", "Ret3"]


def test_praeferencesignal_taeller():
    store.tilfoej_historik("2026-W34", [{"navn": "A"}, {"navn": "B"}], [0])
    store.tilfoej_historik("2026-W35", [{"navn": "A"}, {"navn": "B"}], [0])
    s = store.praeferencesignal()
    assert s["ofte_valgt"]["A"] == 2 and s["ofte_fravalgt"]["B"] == 2


# --- push-abonnementer ------------------------------------------------

def test_abonnement_dubleres_ikke():
    a = {"endpoint": "https://x/1", "keys": {"p256dh": "k", "auth": "a"}}
    assert store.tilfoej_abonnement(a) is True
    assert store.tilfoej_abonnement(a) is False
    assert len(store.hent_abonnementer()) == 1


def test_fjern_abonnement():
    store.tilfoej_abonnement({"endpoint": "https://x/1", "keys": {}})
    assert store.fjern_abonnement("https://x/1") is True
    assert store.fjern_abonnement("https://ukendt") is False
    assert store.hent_abonnementer() == []


# --- bedømmelser ------------------------------------------------------

def test_nedstemte_retter_har_ingen_tidsgraense():
    """En ret man ikke kunne lide bliver ikke bedre af at der går fire uger."""
    for u in ("2026-W20", "2026-W35"):
        store.tilfoej_historik(u, [{"navn": "Ret-%s" % u}], [0])
        store.saet_bedoemmelse(u, "Ret-%s" % u, store.NED)
    assert sorted(store.nedstemte_retter()) == ["Ret-2026-W20", "Ret-2026-W35"]


def test_yndlingsretter_er_kun_dem_med_tommel_op():
    store.tilfoej_historik("2026-W35", [{"navn": "God"}, {"navn": "Skidt"}], [0, 1])
    store.saet_bedoemmelse("2026-W35", "God", store.OP)
    store.saet_bedoemmelse("2026-W35", "Skidt", store.NED)
    assert store.yndlingsretter() == ["God"]
    assert store.nedstemte_retter() == ["Skidt"]


def test_bedoemmelse_paa_ukendt_uge_gaar_stille_ned():
    store.saet_bedoemmelse("2026-W99", "Findes ikke", store.OP)
    assert store.bedoemmelser("2026-W99") == {}


# --- historik_oversigt ------------------------------------------------

def test_oversigt_udelader_pris_naar_ugefilen_er_ryddet():
    """Rækken skal med selvom ugefilen er væk — bare uden pris.

    Nul kroner er en påstand; "vi ved det ikke" er sandheden. Historikken
    holder 52 uger, ugefilerne kan være ryddet længe før.
    """
    store.tilfoej_historik("2026-W10", [{"navn": "Frikadeller"}], [0])
    oversigt = store.historik_oversigt()

    assert [u["uge"] for u in oversigt["uger"]] == ["2026-W10"]
    assert oversigt["uger"][0]["pris"] is None
    assert [r["navn"] for r in oversigt["uger"][0]["retter"]] == ["Frikadeller"]


def test_oversigt_henter_prisen_fra_ugefilen_naar_den_findes():
    uge = store.tom_uge("2026-W11")
    uge.update({
        "forslag": [{"navn": "Frikadeller", "pris_pr_portion": 25, "portioner": 4}],
        "valgt": [0],
    })
    store.gem_uge(uge)
    store.tilfoej_historik("2026-W11", uge["forslag"], [0])

    assert store.historik_oversigt()["uger"][0]["pris"] == 100


def test_oversigt_baerer_ugenoeglen_for_hver_nedstemning():
    """Fortryd-knappen skal kunne ramme /api/uge/{noegle}/bedoem.

    Uden nøglen ved siden ikke hvilken uge bedømmelsen bor i, og en
    fejlklikket tommel ned bliver permanent.
    """
    store.tilfoej_historik("2026-W12", [{"navn": "Blomkålssuppe"}], [0])
    store.saet_bedoemmelse("2026-W12", "Blomkålssuppe", store.NED)

    assert store.historik_oversigt()["nedstemte"] == [
        {"navn": "Blomkålssuppe", "uge": "2026-W12"}
    ]


def test_oversigt_viser_nyeste_uge_foerst():
    store.tilfoej_historik("2026-W10", [{"navn": "A"}], [0])
    store.tilfoej_historik("2026-W12", [{"navn": "B"}], [0])
    assert [u["uge"] for u in store.historik_oversigt()["uger"]] == [
        "2026-W12", "2026-W10"
    ]


def test_tom_oversigt_vaelter_ikke():
    assert store.historik_oversigt() == {
        "uger": [], "nedstemte": [], "ofte_valgt": [], "ofte_fravalgt": []
    }
