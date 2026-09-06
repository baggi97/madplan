"""Fejlmelding og påmindelse.

Går søndagskørslen i fejl kl. 8, står ugen tom til nogen tilfældigvis åbner
websitet. Hele pointen med den automatiske kørsel er at ingen skal holde øje,
så fejlen skal sige sig selv.
"""
from __future__ import annotations

import asyncio

import pytest

from app import flow, store

UGE = "2026-W35"


@pytest.fixture
def sendt(monkeypatch):
    """Opsamler push-beskeder i stedet for at sende dem."""
    beskeder = []

    async def falsk_send(titel, tekst, sti="/"):
        beskeder.append((titel, tekst, sti))

    monkeypatch.setattr(flow.push, "send", falsk_send)
    monkeypatch.setattr(store, "planuge", lambda d=None: UGE)
    return beskeder


def test_fejl_skriver_status_og_sender_push(sendt):
    asyncio.run(flow._fejl(UGE, "REMA svarede ikke"))
    uge = store.hent_uge(UGE)
    assert uge["status"] == store.FEJL
    assert uge["fejlbesked"] == "REMA svarede ikke"
    assert len(sendt) == 1
    titel, tekst, sti = sendt[0]
    assert "REMA svarede ikke" in tekst          # beskeden skal med, ikke "noget gik galt"
    assert sti.endswith(UGE)                     # trykker man, lander man på ugen


def test_fejl_i_hent_forslag_naar_kilden_er_brudt(sendt, monkeypatch):
    async def faa_tilbud():
        return [{"id": "1"}]                     # under MIN_TILBUD

    monkeypatch.setattr(flow.rema, "hent_tilbud", faa_tilbud)
    asyncio.run(flow.hent_forslag())
    assert store.hent_uge(UGE)["status"] == store.FEJL
    assert "REMA" in sendt[0][1]


def test_fejl_naar_ai_kaster(sendt, monkeypatch):
    async def mange_tilbud():
        return [{"id": str(i)} for i in range(50)]

    async def sprang(*a, **kw):
        raise RuntimeError("Anthropic afviste API-nøglen. Tjek ANTHROPIC_API_KEY i .env.")

    monkeypatch.setattr(flow.rema, "hent_tilbud", mange_tilbud)
    monkeypatch.setattr(flow.ai, "foreslaa_retter", sprang)
    asyncio.run(flow.hent_forslag())
    # Den handlingsanvisende besked skal hele vejen ud til telefonen
    assert "ANTHROPIC_API_KEY" in sendt[0][1]


# --- påmindelse -------------------------------------------------------

def _uge_i_vaelger(**ekstra):
    uge = store.tom_uge(UGE)
    uge.update({"status": store.VAELGER, "forslag": [{"navn": "A"}, {"navn": "B"}], **ekstra})
    store.gem_uge(uge)


def test_paamindelse_naar_ingen_har_valgt(sendt):
    _uge_i_vaelger()
    asyncio.run(flow.paamindelse())
    assert len(sendt) == 1
    assert "kl. 18" in sendt[0][1]


def test_ingen_paamindelse_naar_nogen_har_valgt(sendt):
    _uge_i_vaelger(valgt=[0])
    asyncio.run(flow.paamindelse())
    assert sendt == []


def test_egen_ret_taeller_som_et_valg(sendt):
    """Har familien skrevet en egen ret ind, har de valgt — så ingen påmindelse."""
    _uge_i_vaelger(egne=[{"navn": "Tarteletter", "portioner": 4, "valgt": True}])
    asyncio.run(flow.paamindelse())
    assert sendt == []


@pytest.mark.parametrize("status", [store.TOM, store.ARBEJDER, store.KLAR, store.FEJL])
def test_ingen_paamindelse_udenfor_vaelger(sendt, status):
    _uge_i_vaelger()
    store.gem_uge({**store.hent_uge(UGE), "status": status})
    asyncio.run(flow.paamindelse())
    assert sendt == []
