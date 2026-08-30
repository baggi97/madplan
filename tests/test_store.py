"""Datalaget. DATA_DIR peger på en tom mappe pr. test — se conftest."""
from __future__ import annotations

import datetime
import json

from app import config, store


def test_uge_noegle_bruger_isokalender():
    assert store.uge_noegle(datetime.date(2026, 8, 30)) == "2026-W35"
    assert store.uge_nummer("2026-W05") == "5"


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
