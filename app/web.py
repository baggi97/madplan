"""Websitet. Server-renderet med Jinja2 og en håndfuld JSON-endpoints."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, flow, store

log = logging.getLogger(__name__)

HER = Path(__file__).resolve().parent
app = FastAPI(title="Madplan", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HER / "static"), name="static")
skabeloner = Jinja2Templates(directory=str(HER / "templates"))


def _beriget(uge: dict) -> dict:
    """Slår tilbuds-ID'er op, så skabelonerne kan vise varenavne og rabatter."""
    efter_id = {t["id"]: t for t in uge.get("tilbud", [])}
    forslag = []
    for i, ret in enumerate(uge.get("forslag", [])):
        varer = [efter_id[x] for x in ret.get("tilbuds_ids", []) if x in efter_id]
        forslag.append(
            {
                **ret,
                "idx": i,
                "valgt": i in (uge.get("valgt") or []),
                "varer": varer,
                "spar": round(sum(v["normalpris"] - v["pris"] for v in varer)),
            }
        )
    return {**uge, "forslag": forslag}


@app.get("/")
async def forside():
    return RedirectResponse("/uge/" + store.uge_noegle())


@app.get("/uge/{noegle}")
async def uge_side(request: Request, noegle: str):
    uge = _beriget(store.hent_uge(noegle))
    er_denne_uge = noegle == store.uge_noegle()

    if uge["status"] == store.KLAR:
        skabelon = "plan.html"
    elif uge["status"] in (store.VAELGER, store.ARBEJDER, store.FEJL):
        skabelon = "vaelg.html"
    else:
        skabelon = "tom.html"

    return skabeloner.TemplateResponse(
        request,
        skabelon,
        {
            "uge": uge,
            "ugenr": store.uge_nummer(noegle),
            "er_denne_uge": er_denne_uge,
            "antal_valgt": len(uge.get("valgt") or []),
            "arbejder": uge["status"] == store.ARBEJDER,
            "alle_uger": store.alle_uger(),
        },
    )


@app.get("/api/uge/{noegle}/status")
async def status(noegle: str):
    uge = store.hent_uge(noegle)
    return {"status": uge["status"], "fejlbesked": uge["fejlbesked"]}


@app.post("/api/uge/{noegle}/vaelg")
async def vaelg(noegle: str, krop: dict):
    idx = int(krop.get("idx", -1))
    uge = store.hent_uge(noegle)
    if not 0 <= idx < len(uge.get("forslag", [])):
        return JSONResponse({"fejl": "Ukendt ret"}, status_code=400)
    if uge["status"] == store.KLAR:
        return JSONResponse({"fejl": "Ugens madplan er allerede lavet"}, status_code=409)

    valgt = set(uge.get("valgt") or [])
    valgt.symmetric_difference_update({idx})
    uge["valgt"] = sorted(valgt)
    store.gem_uge(uge)
    return {"valgt": uge["valgt"], "antal": len(uge["valgt"])}


@app.post("/api/uge/{noegle}/lav-madplan")
async def start_madplan(noegle: str):
    uge = store.hent_uge(noegle)
    if not uge.get("valgt"):
        return JSONResponse({"fejl": "Vælg mindst én ret først"}, status_code=400)
    asyncio.create_task(flow.lav_madplan(noegle))
    return {"status": store.ARBEJDER}


@app.post("/api/hent-forslag")
async def start_forslag(krop: dict | None = None):
    gennemtving = bool((krop or {}).get("gennemtving"))
    asyncio.create_task(flow.hent_forslag(gennemtving=gennemtving))
    return {"status": store.ARBEJDER}


@app.post("/api/uge/{noegle}/kryds")
async def kryds(noegle: str, krop: dict):
    vare = str(krop.get("vare", ""))
    if not vare:
        return JSONResponse({"fejl": "Mangler vare"}, status_code=400)
    uge = store.hent_uge(noegle)
    afkrydset = uge.get("afkrydset") or {}
    if afkrydset.pop(vare, None) is None:
        afkrydset[vare] = True
    uge["afkrydset"] = afkrydset
    store.gem_uge(uge)
    return {"afkrydset": vare in afkrydset, "antal": len(afkrydset)}


@app.post("/api/uge/{noegle}/nulstil-kryds")
async def nulstil_kryds(noegle: str):
    uge = store.hent_uge(noegle)
    uge["afkrydset"] = {}
    store.gem_uge(uge)
    return {"antal": 0}


@app.get("/sundhedstjek")
async def sundhedstjek():
    return {"ok": True, "uge": store.uge_noegle(), "model": config.ANTHROPIC_MODEL}
