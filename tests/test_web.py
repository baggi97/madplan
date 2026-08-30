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
    assert {"idx": 0, "navn": "Tarteletter", "portioner": 4, "antal": 1}.items() <= r.json().items()
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


# --- samtidige skrivninger --------------------------------------------

def test_ingen_skrivninger_tabes(vaelger_uge):
    """To samtidige ændringer må ikke overskrive hinanden.

    Mutationsfunktionen her har et await midt i læs-ret-skriv. Uden låsen i
    _opdater skifter event-loopet der, begge coroutiner skriver den uge de
    læste, og den enes ændring forsvinder. Testen fejler uden låsen.
    """
    import asyncio

    async def saet(idx):
        async def aendring(uge):
            valgt = set(uge.get("valgt") or [])
            await asyncio.sleep(0)      # her ville racen opstå
            valgt.add(idx)
            uge["valgt"] = sorted(valgt)
            return {"valgt": uge["valgt"]}

        return await web._opdater(UGE, aendring)

    async def go():
        await asyncio.gather(*(saet(i) for i in range(3)))

    asyncio.run(go())
    assert store.hent_uge(UGE)["valgt"] == [0, 1, 2]


def test_afvist_aendring_gemmes_ikke(klient, vaelger_uge):
    """En 400 eller 409 må ikke efterlade en halvt ændret uge på disken."""
    store.gem_uge({**store.hent_uge(UGE), "status": store.KLAR, "valgt": [1]})
    assert klient.post(f"/api/uge/{UGE}/vaelg", json={"idx": 0}).status_code == 409
    assert store.hent_uge(UGE)["valgt"] == [1]


# --- ugedag -----------------------------------------------------------

def test_dag_saettes_og_gemmes(klient, vaelger_uge):
    r = klient.post(f"/api/uge/{UGE}/dag", json={"slags": "forslag", "idx": 0, "dag": "torsdag"})
    assert r.json()["dag"] == "torsdag"
    assert store.hent_uge(UGE)["forslag"][0]["dag"] == "torsdag"


def test_dag_kan_ryddes(klient, vaelger_uge):
    klient.post(f"/api/uge/{UGE}/dag", json={"slags": "forslag", "idx": 0, "dag": "torsdag"})
    klient.post(f"/api/uge/{UGE}/dag", json={"slags": "forslag", "idx": 0, "dag": ""})
    assert store.hent_uge(UGE)["forslag"][0]["dag"] == ""


@pytest.mark.parametrize("dag", ["mandagx", "monday", "17"])
def test_ukendt_dag_afvises(klient, vaelger_uge, dag):
    r = klient.post(f"/api/uge/{UGE}/dag", json={"slags": "forslag", "idx": 0, "dag": dag})
    assert r.status_code == 400
    assert "dag" not in store.hent_uge(UGE)["forslag"][0]


def test_dag_paa_egen_ret(klient, vaelger_uge):
    klient.post(f"/api/uge/{UGE}/egen", json={"navn": "Tarteletter"})
    klient.post(f"/api/uge/{UGE}/dag", json={"slags": "egen", "idx": 0, "dag": "fredag"})
    assert store.hent_uge(UGE)["egne"][0]["dag"] == "fredag"


def test_opskrifter_sorteres_efter_dag():
    """Opskrifterne kommer fra kald 2 og kender ikke dagen — den kobles på navnet."""
    uge = store.tom_uge(UGE)
    uge.update({
        "status": store.KLAR,
        "forslag": [ret("Fisk", dag="fredag"), ret("Suppe", dag="mandag"), ret("Steg")],
        "valgt": [0, 1, 2],
        "madplan": {"opskrifter": [{"navn": "Fisk"}, {"navn": "Suppe"}, {"navn": "Steg"}],
                    "indkoebsliste": []},
    })
    ud = web._beriget(uge)["madplan"]["opskrifter"]
    # mandag før fredag, og den uden dag sidst
    assert [o["navn"] for o in ud] == ["Suppe", "Fisk", "Steg"]
    assert ud[2]["dag"] == ""


def test_ukendt_opskriftsnavn_forsvinder_ikke():
    uge = store.tom_uge(UGE)
    uge.update({
        "status": store.KLAR,
        "forslag": [ret("Fisk", dag="fredag")],
        "valgt": [0],
        "madplan": {"opskrifter": [{"navn": "Noget helt andet"}], "indkoebsliste": []},
    })
    ud = web._beriget(uge)["madplan"]["opskrifter"]
    assert len(ud) == 1 and ud[0]["dag"] == ""


# --- ugens pris -------------------------------------------------------

def test_totaler_regner_pris_og_besparelse(vaelger_uge):
    uge = store.hent_uge(UGE)
    uge["forslag"][0].update({"pris_pr_portion": 30, "portioner": 4, "tilbuds_ids": ["1"]})
    uge["forslag"][1].update({"pris_pr_portion": 20, "portioner": 2, "tilbuds_ids": []})
    uge["valgt"] = [0, 1]
    store.gem_uge(uge)
    t = web._totaler(store.hent_uge(UGE))
    assert t["antal"] == 2
    assert t["pris"] == 30 * 4 + 20 * 2        # 160
    assert t["spar"] == 10                     # 35 - 25 på tilbud "1"


def test_totaler_tomme_naar_intet_er_valgt(vaelger_uge):
    assert web._totaler(store.hent_uge(UGE)) == {"antal": 0, "pris": 0, "spar": 0}


def test_egne_retter_taeller_med_i_prisen(klient, vaelger_uge):
    klient.post(f"/api/uge/{UGE}/egen", json={"navn": "Tarteletter"})
    uge = store.hent_uge(UGE)
    uge["egne"][0]["pris_pr_portion"] = 25
    store.gem_uge(uge)
    assert web._totaler(store.hent_uge(UGE))["pris"] == 100     # 25 × 4 portioner


def test_vaelg_returnerer_totaler(klient, vaelger_uge):
    svar = klient.post(f"/api/uge/{UGE}/vaelg", json={"idx": 0}).json()
    assert {"antal", "pris", "spar"} <= set(svar)


def test_plan_siden_viser_pris_og_dage(klient):
    """Hele vejen fra state til renderet HTML."""
    uge = store.tom_uge(UGE)
    uge.update({
        "status": store.KLAR,
        "tilbud": [{"id": "1", "navn": "Kød", "pris": 25.0, "normalpris": 35.0, "rabat_pct": 29}],
        "forslag": [ret("Fisk", ids=["1"], dag="fredag", portioner=4, pris=30),
                    ret("Suppe", dag="mandag", portioner=2, pris=20)],
        "valgt": [0, 1],
        "madplan": {
            "opskrifter": [
                {"navn": "Fisk", "portioner": 4, "tid_min": 20,
                 "ingredienser": ["a"], "fremgangsmaade": ["b"]},
                {"navn": "Suppe", "portioner": 2, "tid_min": 15,
                 "ingredienser": ["c"], "fremgangsmaade": ["d"]},
            ],
            "indkoebsliste": [{"afdeling": "Køl", "varer": [{"vare": "Kød", "paa_tilbud": True}]}],
        },
    })
    store.gem_uge(uge)
    html = klient.get(f"/uge/{UGE}").text
    assert "ca. 160 kr." in html            # 30×4 + 20×2
    assert "Tilbuddene sparer 10 kr." in html
    assert "Mandag" in html and "Fredag" in html
    assert html.index("Suppe") < html.index("Fisk")   # mandag før fredag


# --- rester -----------------------------------------------------------

def _uge_med_madplan(**madplan_ekstra):
    uge = store.tom_uge(UGE)
    uge.update({
        "status": store.KLAR,
        "forslag": [ret("Karry", portioner=4)],
        "valgt": [0],
        "madplan": {
            "opskrifter": [{"navn": "Karry", "portioner": 4, "tid_min": 20,
                            "ingredienser": ["a"], "fremgangsmaade": ["b"]}],
            "indkoebsliste": [{"afdeling": "Kolonial",
                               "varer": [{"vare": "1 dåse kokosmælk", "paa_tilbud": False}]}],
            **madplan_ekstra,
        },
    })
    store.gem_uge(uge)


def test_rester_vises(klient):
    _uge_med_madplan(rester=[{"vare": "en halv dåse kokosmælk",
                             "forslag": "brug den i torsdagens suppe"}])
    html = klient.get(f"/uge/{UGE}").text
    assert "Rester at bruge" in html
    assert "en halv dåse kokosmælk" in html
    assert "torsdagens suppe" in html


def test_ingen_rester_giver_ingen_sektion(klient):
    _uge_med_madplan(rester=[])
    assert "Rester at bruge" not in klient.get(f"/uge/{UGE}").text


def test_gammel_uge_uden_rester_renderer_stadig(klient):
    """Feltet er ikke required — uger gemt før ændringen må ikke vælte."""
    _uge_med_madplan()
    r = klient.get(f"/uge/{UGE}")
    assert r.status_code == 200
    assert "Rester at bruge" not in r.text
