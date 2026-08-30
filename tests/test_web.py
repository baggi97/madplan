"""Endpoints gennem FastAPI's testklient. Ingen AI-kald, intet netværk."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import store, web
from tests.conftest import ret

UGE = "2026-W35"


@pytest.fixture
def klient(monkeypatch):
    monkeypatch.setattr(store, "uge_noegle", lambda d=None: UGE)
    return TestClient(web.app)


@pytest.fixture
def vaelger_uge():
    """En uge med tre forslag, klar til at blive valgt i."""
    uge = store.tom_uge(UGE)
    uge.update({
        "status": store.VAELGER,
        "tilbud": [{"id": "1", "navn": "Kød", "pris": 25.0, "normalpris": 35.0, "rabat_pct": 29}],
        "forslag": [ret("A", ids=["1"], portioner=4), ret("B", portioner=4), ret("C", portioner=4)],
    })
    store.gem_uge(uge)
    return uge


def test_sundhedstjek(klient):
    assert klient.get("/sundhedstjek").json()["ok"] is True


def test_forside_redirecter_til_denne_uge(klient):
    r = klient.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith(UGE)


# --- valg -------------------------------------------------------------

def test_vaelg_slaar_til_og_fra(klient, vaelger_uge):
    assert klient.post(f"/api/uge/{UGE}/vaelg", json={"idx": 0}).json()["valgt"] == [0]
    assert klient.post(f"/api/uge/{UGE}/vaelg", json={"idx": 0}).json()["valgt"] == []


def test_vaelg_ugyldigt_indeks(klient, vaelger_uge):
    assert klient.post(f"/api/uge/{UGE}/vaelg", json={"idx": 99}).status_code == 400


def test_vaelg_afvises_naar_planen_er_lavet(klient, vaelger_uge):
    store.gem_uge({**store.hent_uge(UGE), "status": store.KLAR})
    assert klient.post(f"/api/uge/{UGE}/vaelg", json={"idx": 0}).status_code == 409


# --- portioner --------------------------------------------------------

@pytest.mark.parametrize("antal, kode", [(1, 200), (12, 200), (0, 400), (13, 400)])
def test_portionsgraenser(klient, vaelger_uge, antal, kode):
    r = klient.post(f"/api/uge/{UGE}/portioner",
                    json={"slags": "forslag", "idx": 0, "portioner": antal})
    assert r.status_code == kode


def test_portioner_gemmes(klient, vaelger_uge):
    klient.post(f"/api/uge/{UGE}/portioner",
                json={"slags": "forslag", "idx": 1, "portioner": 6})
    assert store.hent_uge(UGE)["forslag"][1]["portioner"] == 6


# --- egne retter ------------------------------------------------------

def test_egen_ret_tilfoejes_og_taelles_med(klient, vaelger_uge):
    r = klient.post(f"/api/uge/{UGE}/egen", json={"navn": "Tarteletter"})
    assert r.json() == {"idx": 0, "navn": "Tarteletter", "portioner": 4, "antal": 1}
    # Egne retter tæller med i "antal valgt" — brug _antal_valgt, ikke len(valgt)
    assert web._antal_valgt(store.hent_uge(UGE)) == 1


@pytest.mark.parametrize("navn", ["", "   "])
def test_egen_ret_kraever_navn(klient, vaelger_uge, navn):
    assert klient.post(f"/api/uge/{UGE}/egen", json={"navn": navn}).status_code == 400


def test_egen_ret_dubleres_ikke(klient, vaelger_uge):
    klient.post(f"/api/uge/{UGE}/egen", json={"navn": "Tarteletter"})
    r = klient.post(f"/api/uge/{UGE}/egen", json={"navn": "TARTELETTER"})
    assert r.status_code == 400


def test_egne_retter_overlever_nye_forslag(klient, vaelger_uge):
    """valgt er indeks i forslag, så egne retter ligger bevidst udenfor."""
    klient.post(f"/api/uge/{UGE}/egen", json={"navn": "Tarteletter"})
    uge = store.hent_uge(UGE)
    uge.update({"forslag": [ret("Ny")], "valgt": []})   # som hent_forslag gør
    store.gem_uge(uge)
    assert [e["navn"] for e in store.hent_uge(UGE)["egne"]] == ["Tarteletter"]


def test_slet_egen_ret(klient, vaelger_uge):
    klient.post(f"/api/uge/{UGE}/egen", json={"navn": "Tarteletter"})
    assert klient.post(f"/api/uge/{UGE}/egen/slet", json={"idx": 0}).json()["antal"] == 0
    assert klient.post(f"/api/uge/{UGE}/egen/slet", json={"idx": 0}).status_code == 400


# --- madplan ----------------------------------------------------------

def test_lav_madplan_kraever_et_valg(klient, vaelger_uge):
    assert klient.post(f"/api/uge/{UGE}/lav-madplan").status_code == 400


def test_egen_ret_alene_er_nok(klient, vaelger_uge, monkeypatch):
    monkeypatch.setattr(web.asyncio, "create_task", lambda k: k.close())
    klient.post(f"/api/uge/{UGE}/egen", json={"navn": "Tarteletter"})
    assert klient.post(f"/api/uge/{UGE}/lav-madplan").status_code == 200


# --- afkrydsning ------------------------------------------------------

def test_kryds_slaar_til_og_fra(klient, vaelger_uge):
    assert klient.post(f"/api/uge/{UGE}/kryds", json={"vare": "g0v0"}).json()["afkrydset"] is True
    assert klient.post(f"/api/uge/{UGE}/kryds", json={"vare": "g0v0"}).json()["afkrydset"] is False


def test_kryds_kraever_vare(klient, vaelger_uge):
    assert klient.post(f"/api/uge/{UGE}/kryds", json={}).status_code == 400


# --- push -------------------------------------------------------------

def test_abonnement_kraever_https(klient):
    r = klient.post("/api/push/abonner",
                    json={"endpoint": "http://x/y", "keys": {"p256dh": "k"}})
    assert r.status_code in (400, 503)


def test_service_worker_serveres_fra_roden(klient):
    """Fra /static/sw.js ville workeren kun gælde for /static/."""
    r = klient.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
