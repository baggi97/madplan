"""Endpoints gennem FastAPI's testklient. Ingen AI-kald, intet netværk."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import store, web
from tests.conftest import ret

UGE = "2026-W35"


@pytest.fixture
def klient(monkeypatch):
    monkeypatch.setattr(store, "planuge", lambda d=None: UGE)
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


def test_statiske_filer_faar_en_version(klient, vaelger_uge):
    """Uden ?v= kan en designrettelse vaere usynlig bag browserens cache."""
    html = klient.get(f"/uge/{UGE}").text
    assert "/static/app.css?v=" in html
    assert "/static/app.js?v=" in html


def test_versionen_skifter_naar_filen_goer(tmp_path):
    fil = tmp_path / "app.css"
    fil.write_text("a", encoding="utf-8")
    foer = web._statisk_version([fil])
    import os, time
    os.utime(fil, ns=(0, int(time.time_ns()) + 10**9))
    assert web._statisk_version([fil]) != foer


def test_version_uden_filer_vaelter_ikke(tmp_path):
    assert web._statisk_version([tmp_path / "findes-ikke.css"]) == "0"


# --- live-opdatering --------------------------------------------------

def test_live_giver_den_delte_tilstand(klient, vaelger_uge):
    klient.post(f"/api/uge/{UGE}/vaelg", json={"idx": 1})
    klient.post(f"/api/uge/{UGE}/kryds", json={"vare": "g0v0"})
    d = klient.get(f"/api/uge/{UGE}/live").json()
    assert d["valgt"] == [1]
    assert d["afkrydset"] == ["g0v0"]
    assert {"status", "antal", "pris", "spar", "egne_valgt", "antal_egne"} <= set(d)


def test_live_melder_antal_egne_saa_klienten_kan_genindlaese(klient, vaelger_uge):
    """Kortene for egne retter findes ikke i DOM'en før en genindlæsning, så
    klienten skal kunne se at antallet har ændret sig."""
    assert klient.get(f"/api/uge/{UGE}/live").json()["antal_egne"] == 0
    klient.post(f"/api/uge/{UGE}/egen", json={"navn": "Tarteletter"})
    assert klient.get(f"/api/uge/{UGE}/live").json()["antal_egne"] == 1


# --- bedømmelse -------------------------------------------------------

def _uge_spist():
    store.tilfoej_historik(UGE, [{"navn": "Karry"}, {"navn": "Fisk"}], [0, 1])


def test_bedoemmelse_gemmes_i_historikken(klient):
    _uge_spist()
    r = klient.post(f"/api/uge/{UGE}/bedoem", json={"navn": "Karry", "vurdering": "op"})
    assert r.json() == {"navn": "Karry", "vurdering": "op"}
    assert store.bedoemmelser(UGE) == {"Karry": "op"}


def test_samme_tryk_igen_fjerner_bedoemmelsen(klient):
    _uge_spist()
    klient.post(f"/api/uge/{UGE}/bedoem", json={"navn": "Karry", "vurdering": "op"})
    r = klient.post(f"/api/uge/{UGE}/bedoem", json={"navn": "Karry", "vurdering": "op"})
    assert r.json()["vurdering"] is None
    assert store.bedoemmelser(UGE) == {}


def test_skift_fra_op_til_ned(klient):
    _uge_spist()
    klient.post(f"/api/uge/{UGE}/bedoem", json={"navn": "Karry", "vurdering": "op"})
    klient.post(f"/api/uge/{UGE}/bedoem", json={"navn": "Karry", "vurdering": "ned"})
    assert store.bedoemmelser(UGE) == {"Karry": "ned"}


@pytest.mark.parametrize("krop", [{"navn": "", "vurdering": "op"},
                                  {"navn": "Karry", "vurdering": "måske"}])
def test_ugyldig_bedoemmelse_afvises(klient, krop):
    _uge_spist()
    assert klient.post(f"/api/uge/{UGE}/bedoem", json=krop).status_code == 400


# --- historiksiden ----------------------------------------------------

def test_historik_renderer_uden_historik(klient):
    svar = klient.get("/historik")
    assert svar.status_code == 200
    assert "Der er ikke lavet nogen madplaner endnu" in svar.text


def test_historik_viser_ugen_og_bedoemmelsen(klient):
    store.tilfoej_historik(UGE, [ret("Frikadeller"), ret("Fiskefrikadeller")], [0])
    store.saet_bedoemmelse(UGE, "Frikadeller", store.OP)

    tekst = klient.get("/historik").text
    assert "Frikadeller" in tekst
    assert "Uge 35" in tekst


def test_nedstemt_ret_faar_en_fortrydknap(klient):
    store.tilfoej_historik(UGE, [ret("Blomkålssuppe")], [0])
    store.saet_bedoemmelse(UGE, "Blomkålssuppe", store.NED)

    tekst = klient.get("/historik").text
    assert "Fortryd" in tekst
    assert 'data-navn="Blomkålssuppe"' in tekst


def test_fortryd_faar_retten_tilbage_i_spil(klient):
    """Hele pointen med siden.

    `nedstemte_retter()` har ingen tidsgrænse, så uden denne vej ud er et
    fejlklik en permanent udelukkelse man skal ind i en JSON-fil for at hæve.
    """
    store.tilfoej_historik(UGE, [ret("Blomkålssuppe")], [0])
    store.saet_bedoemmelse(UGE, "Blomkålssuppe", store.NED)
    assert "Blomkålssuppe" in store.nedstemte_retter()

    svar = klient.post(
        "/api/uge/{}/bedoem".format(UGE),
        json={"navn": "Blomkålssuppe", "vurdering": "ned"},
    )
    assert svar.status_code == 200
    assert store.nedstemte_retter() == []
    assert store.historik_oversigt()["nedstemte"] == []


# --- linjen om frasorterede forslag -----------------------------------

def test_frasorteret_vises_naar_der_kom_for_faa(klient, monkeypatch):
    """Grunden til at der kom 8 og ikke 10 skal kunne læses på siden,
    ikke kun i en log inde i containeren på NAS'en."""
    monkeypatch.setattr(web.config, "ANTAL_FORSLAG", 5)
    uge = store.tom_uge(UGE)
    uge.update({
        "status": store.VAELGER,
        "forslag": [ret("A"), ret("B")],
        "frasorteret": {"gentagelser": 2, "kostreglerne": 1},
    })
    store.gem_uge(uge)

    tekst = klient.get("/uge/{}".format(UGE)).text
    assert "Vi bad om 5 og fik 2" in tekst
    assert "2 på gentagelser" in tekst
    assert "1 på kostreglerne" in tekst


def test_frasorteret_er_tavs_naar_alle_forslag_kom(klient, monkeypatch):
    """Kom der det antal der blev bedt om, er linjen bare støj."""
    monkeypatch.setattr(web.config, "ANTAL_FORSLAG", 2)
    uge = store.tom_uge(UGE)
    uge.update({
        "status": store.VAELGER,
        "forslag": [ret("A"), ret("B")],
        "frasorteret": {"gentagelser": 2},
    })
    store.gem_uge(uge)

    assert "Vi bad om" not in klient.get("/uge/{}".format(UGE)).text
