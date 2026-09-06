"""Websitet. Server-renderet med Jinja2 og en håndfuld JSON-endpoints."""
from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, flow, push, store

log = logging.getLogger(__name__)

HER = Path(__file__).resolve().parent
app = FastAPI(title="Madplan", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HER / "static"), name="static")
skabeloner = Jinja2Templates(directory=str(HER / "templates"))

DAGE = ("mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag")


def _statisk_version(stier: list | None = None) -> str:
    """Skifter når app.css eller app.js gør.

    Hænges på som ?v= i skabelonen. Uden det serveres filerne kun med ETag, og
    browseren må selv gætte hvor længe den holder på dem — så en designrettelse
    kan være usynlig indtil nogen tømmer cachen. På en telefon med siden på
    hjemmeskærmen er det ikke ligetil.

    Beregnes ved import, så en genstart efter en opdatering giver en ny værdi.
    """
    stier = stier or [HER / "static" / "app.css", HER / "static" / "app.js"]
    seneste = max((s.stat().st_mtime_ns for s in stier if s.exists()), default=0)
    return format(seneste, "x")[-8:]


STATISK_VERSION = _statisk_version()


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
    egne = [
        {**e, "idx": i, "portioner": e.get("portioner") or 4, "dag": e.get("dag", "")}
        for i, e in enumerate(uge.get("egne") or [])
    ]
    return {**uge, "forslag": forslag, "egne": egne, "madplan": _med_dage(uge)}


def _med_dage(uge: dict) -> dict:
    """Hænger den valgte ugedag på hver opskrift og sorterer efter den.

    Opskrifterne kommer fra kald 2 og kender ikke dagen — den er valgt i
    websitet. Vi kobler på navnet, som modellen bliver bedt om at genbruge.
    Matcher et navn ikke, ryger retten bagerst uden dag frem for at forsvinde.
    """
    madplan = uge.get("madplan") or {}
    opskrifter = madplan.get("opskrifter")
    if not opskrifter:
        return madplan

    dag_for = {
        r["navn"]: r.get("dag", "")
        for r in (uge.get("forslag") or []) + (uge.get("egne") or [])
        if r.get("navn")
    }
    beriget = [{**o, "dag": dag_for.get(o.get("navn"), "")} for o in opskrifter]
    # Uden dag sidst; ellers ugens rækkefølge
    beriget.sort(key=lambda o: DAGE.index(o["dag"]) if o["dag"] in DAGE else len(DAGE))
    return {**madplan, "opskrifter": beriget}


# Alle skrivninger af uge-filen går gennem én lås.
#
# Som koden ser ud i dag er der ingen race: endpointsene er `async def`, men
# har intet `await` mellem læsning og skrivning, så event-loopet kan ikke
# skifte midt i. Det er efterprøvet — se test_ingen_skrivninger_tabes.
#
# Det holder kun så længe ingen indsætter et `await` i den blok. Gør nogen det
# — en push-besked, et opslag, hvad som helst — taber to samtidige klik den
# enes skrivning, tavst. Låsen gør invarianten uafhængig af det, og samler
# samtidig læs-ret-skriv ét sted i stedet for syv.
#
# Bevidst IKKE flow._laas: den holdes hen over AI-kaldene i op til halvandet
# minut, og så ville hele websitet fryse imens.
_skrivelaas = asyncio.Lock()


async def _opdater(noegle: str, aendring):
    """Læs uge, lad `aendring` rette i den, skriv tilbage — under lås.

    Returnerer `aendring` en JSONResponse, er ændringen afvist, og ugen
    gemmes ikke. Ellers sendes returværdien videre til klienten.
    """
    async with _skrivelaas:
        uge = store.hent_uge(noegle)
        svar = aendring(uge)
        if inspect.isawaitable(svar):
            svar = await svar
        if isinstance(svar, JSONResponse):
            return svar
        store.gem_uge(uge)
        return svar


def _afvis(besked: str, kode: int = 400) -> JSONResponse:
    return JSONResponse({"fejl": besked}, status_code=kode)


def _laast(uge: dict) -> JSONResponse | None:
    if uge["status"] == store.KLAR:
        return _afvis("Ugens madplan er allerede lavet", 409)
    return None


def _spar(ret: dict, efter_id: dict) -> int:
    varer = [efter_id[x] for x in (ret.get("tilbuds_ids") or []) if x in efter_id]
    return round(sum(v["normalpris"] - v["pris"] for v in varer))


def _totaler(uge: dict) -> dict:
    """Antal valgte, anslået pris for ugen, og hvad tilbuddene sparer.

    Prisen er **et skøn**: `pris_pr_portion` er modellens vurdering, ikke en
    beregning ud fra tilbudspriserne. Samme forbehold som `dyre`-mærkatet.
    Besparelsen er derimod rigtige tal fra REMA.
    """
    efter_id = {t["id"]: t for t in uge.get("tilbud", [])}
    valgte = [
        r for i, r in enumerate(uge.get("forslag") or []) if i in (uge.get("valgt") or [])
    ] + [e for e in (uge.get("egne") or []) if e.get("valgt")]

    pris = sum(
        float(r.get("pris_pr_portion") or 0) * int(r.get("portioner") or 0) for r in valgte
    )
    return {
        "antal": len(valgte),
        "pris": round(pris),
        "spar": sum(_spar(r, efter_id) for r in valgte),
    }


MIN_PORTIONER, MAKS_PORTIONER = 1, 12
MAKS_EGNE = 10
MAKS_NAVN = 80


def _egne(uge: dict) -> list:
    return uge.get("egne") or []


def _antal_valgt(uge: dict) -> int:
    return store.antal_valgt(uge)


@app.get("/")
async def forside():
    return RedirectResponse("/uge/" + store.planuge())


@app.get("/uge/{noegle}")
async def uge_side(request: Request, noegle: str):
    uge = _beriget(store.hent_uge(noegle))
    er_denne_uge = noegle == store.planuge()

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
            "antal_valgt": _antal_valgt(uge),
            "dage": DAGE,
            "v": STATISK_VERSION,
            "totaler": _totaler(uge),
            "bedoemt": store.bedoemmelser(noegle),
            "arbejder": uge["status"] == store.ARBEJDER,
            "alle_uger": store.alle_uger(),
        },
    )


@app.get("/api/uge/{noegle}/status")
async def status(noegle: str):
    uge = store.hent_uge(noegle)
    return {"status": uge["status"], "fejlbesked": uge["fejlbesked"]}


@app.post("/api/uge/{noegle}/bedoem")
async def bedoem(noegle: str, krop: dict):
    """Tommel op eller ned efter måltidet.

    Gemmes i historikken, ikke i ugefilen — det er derfra signalet til
    fremtidige forslag læses. Samme vurdering igen fjerner den, så man kan
    fortryde.
    """
    navn = str(krop.get("navn", "")).strip()
    vurdering = krop.get("vurdering")
    if not navn:
        return _afvis("Mangler ret")
    if vurdering not in (store.OP, store.NED, None):
        return _afvis("Ukendt bedømmelse")

    nuvaerende = store.bedoemmelser(noegle).get(navn)
    ny_vurdering = None if nuvaerende == vurdering else vurdering
    store.saet_bedoemmelse(noegle, navn, ny_vurdering)
    return {"navn": navn, "vurdering": ny_vurdering}


@app.get("/api/uge/{noegle}/live")
async def live(noegle: str):
    """Den delte tilstand, så flere telefoner kan følge med i hinanden.

    README lover at alle i huset vælger fra hver sin telefon. Det passede,
    men man så først de andres valg efter en genindlæsning. Klienten patcher
    DOM'en med det her frem for at genindlæse — ingen skal miste sin plads
    på indkøbslisten midt i butikken.
    """
    uge = store.hent_uge(noegle)
    return {
        "status": uge["status"],
        "opdateret": uge.get("opdateret", ""),
        "valgt": uge.get("valgt") or [],
        "egne_valgt": [
            i for i, e in enumerate(uge.get("egne") or []) if e.get("valgt")
        ],
        "antal_egne": len(uge.get("egne") or []),
        "afkrydset": sorted(uge.get("afkrydset") or {}),
        **_totaler(uge),
    }


@app.post("/api/uge/{noegle}/vaelg")
async def vaelg(noegle: str, krop: dict):
    idx = int(krop.get("idx", -1))

    def aendring(uge):
        if not 0 <= idx < len(uge.get("forslag", [])):
            return _afvis("Ukendt ret")
        if afvist := _laast(uge):
            return afvist
        valgt = set(uge.get("valgt") or [])
        valgt.symmetric_difference_update({idx})
        uge["valgt"] = sorted(valgt)
        return {"valgt": uge["valgt"], **_totaler(uge)}

    return await _opdater(noegle, aendring)


@app.post("/api/uge/{noegle}/portioner")
async def saet_portioner(noegle: str, krop: dict):
    """Antal personer pr. ret — både på forslag og på familiens egne retter."""
    try:
        idx = int(krop.get("idx", -1))
        antal = int(krop.get("portioner", 0))
    except (TypeError, ValueError):
        return _afvis("Ugyldigt antal")

    if not MIN_PORTIONER <= antal <= MAKS_PORTIONER:
        return _afvis(
            "Vælg mellem {} og {} personer".format(MIN_PORTIONER, MAKS_PORTIONER)
        )

    def aendring(uge):
        if afvist := _laast(uge):
            return afvist
        liste = _egne(uge) if krop.get("slags") == "egen" else uge.get("forslag") or []
        if not 0 <= idx < len(liste):
            return _afvis("Ukendt ret")
        liste[idx]["portioner"] = antal
        return {"portioner": antal, **_totaler(uge)}

    return await _opdater(noegle, aendring)


@app.post("/api/uge/{noegle}/dag")
async def saet_dag(noegle: str, krop: dict):
    """Hvilken aften retten ligger på. Tom streng betyder ingen dag valgt."""
    dag = str(krop.get("dag", "")).strip().lower()
    if dag and dag not in DAGE:
        return _afvis("Ukendt ugedag")
    try:
        idx = int(krop.get("idx", -1))
    except (TypeError, ValueError):
        return _afvis("Ukendt ret")

    def aendring(uge):
        if afvist := _laast(uge):
            return afvist
        liste = _egne(uge) if krop.get("slags") == "egen" else uge.get("forslag") or []
        if not 0 <= idx < len(liste):
            return _afvis("Ukendt ret")
        liste[idx]["dag"] = dag
        return {"dag": dag}

    return await _opdater(noegle, aendring)


@app.post("/api/uge/{noegle}/egen")
async def tilfoej_egen(noegle: str, krop: dict):
    """Familiens eget ønske — en titel der følger med til madplanen."""
    navn = str(krop.get("navn", "")).strip()[:MAKS_NAVN]
    if not navn:
        return _afvis("Skriv hvad retten hedder")
    standard = config.hent_praeferencer().get("standard_portioner", 4)

    def aendring(uge):
        if afvist := _laast(uge):
            return afvist
        egne = _egne(uge)
        if len(egne) >= MAKS_EGNE:
            return _afvis("Der er plads til {} egne retter".format(MAKS_EGNE))
        if any(e["navn"].lower() == navn.lower() for e in egne):
            return _afvis("Den ret står der allerede")
        egne.append({"navn": navn, "portioner": standard, "valgt": True})
        uge["egne"] = egne
        return {
            "idx": len(egne) - 1,
            "navn": navn,
            "portioner": standard,
            **_totaler(uge),
        }

    return await _opdater(noegle, aendring)


@app.post("/api/uge/{noegle}/egen/vaelg")
async def vaelg_egen(noegle: str, krop: dict):
    idx = int(krop.get("idx", -1))

    def aendring(uge):
        if afvist := _laast(uge):
            return afvist
        egne = _egne(uge)
        if not 0 <= idx < len(egne):
            return _afvis("Ukendt ret")
        egne[idx]["valgt"] = not egne[idx].get("valgt")
        return {"valgt": egne[idx]["valgt"], **_totaler(uge)}

    return await _opdater(noegle, aendring)


@app.post("/api/uge/{noegle}/egen/slet")
async def slet_egen(noegle: str, krop: dict):
    idx = int(krop.get("idx", -1))

    def aendring(uge):
        if afvist := _laast(uge):
            return afvist
        egne = _egne(uge)
        if not 0 <= idx < len(egne):
            return _afvis("Ukendt ret")
        egne.pop(idx)
        uge["egne"] = egne
        return _totaler(uge)

    return await _opdater(noegle, aendring)


@app.post("/api/uge/{noegle}/lav-madplan")
async def start_madplan(noegle: str):
    uge = store.hent_uge(noegle)
    if not _antal_valgt(uge):
        return _afvis("Vælg mindst én ret først")
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
        return _afvis("Mangler vare")

    def aendring(uge):
        afkrydset = uge.get("afkrydset") or {}
        if afkrydset.pop(vare, None) is None:
            afkrydset[vare] = True
        uge["afkrydset"] = afkrydset
        return {"afkrydset": vare in afkrydset, "antal": len(afkrydset)}

    return await _opdater(noegle, aendring)


@app.post("/api/uge/{noegle}/nulstil-kryds")
async def nulstil_kryds(noegle: str):
    def aendring(uge):
        uge["afkrydset"] = {}
        return {"antal": 0}

    return await _opdater(noegle, aendring)


# --- Push -------------------------------------------------------------
#
# Service workeren SKAL serveres fra roden. Ligger den på /static/sw.js,
# gælder den kun for /static/ og ser aldrig resten af websitet.

@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        HER / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/manifest.json")
async def manifest():
    return FileResponse(HER / "static" / "manifest.json", media_type="application/manifest+json")


@app.get("/api/push/noegle")
async def push_noegle():
    """Den offentlige VAPID-nøgle browseren skal abonnere med."""
    return {
        "noegle": config.VAPID_OFFENTLIG_NOEGLE,
        "slaaet_til": push.slaaet_til(),
    }


@app.post("/api/push/abonner")
async def push_abonner(krop: dict):
    if not push.slaaet_til():
        return JSONResponse(
            {"fejl": "Push er ikke sat op på serveren. Se VAPID-nøglerne i .env."},
            status_code=503,
        )
    if not str(krop.get("endpoint", "")).startswith("https://"):
        return JSONResponse({"fejl": "Ugyldigt abonnement"}, status_code=400)
    if not (krop.get("keys") or {}).get("p256dh"):
        return JSONResponse({"fejl": "Abonnementet mangler nøgler"}, status_code=400)

    ny = store.tilfoej_abonnement(
        {"endpoint": krop["endpoint"], "keys": krop["keys"]}
    )
    log.info("Push-abonnement %s", "tilføjet" if ny else "fandtes allerede")
    return {"ny": ny, "antal": len(store.hent_abonnementer())}


@app.post("/api/push/afmeld")
async def push_afmeld(krop: dict):
    fjernet = store.fjern_abonnement(str(krop.get("endpoint", "")))
    return {"fjernet": fjernet, "antal": len(store.hent_abonnementer())}


@app.post("/api/push/proeve")
async def push_proeve():
    """Sender en prøvebesked, så man kan se at det virker uden at vente til
    søndag."""
    if not push.slaaet_til():
        return JSONResponse({"fejl": "Push er ikke sat op på serveren."}, status_code=503)
    if not store.hent_abonnementer():
        return JSONResponse({"fejl": "Ingen har slået beskeder til endnu."}, status_code=400)
    await push.send("Madplan", "Sådan ser en besked ud. Alt virker.", "/")
    return {"sendt": len(store.hent_abonnementer())}


@app.get("/sundhedstjek")
async def sundhedstjek():
    return {"ok": True, "uge": store.planuge(), "model": config.ANTHROPIC_MODEL}
