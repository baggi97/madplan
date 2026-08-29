# Madplan

Selvstændig Docker-container: henter REMA 1000's ugentlige tilbud, lader Claude
foreslå ti retter bygget på dem, familien vælger via et website, og der
genereres opskrifter plus en delt indkøbsliste.

Ingen Home Assistant, ingen eksterne afhængigheder ud over Anthropic API'et.

## Kør lokalt

```bash
pip install -r requirements.txt
cp .env.example .env          # udfyld ANTHROPIC_API_KEY
python -m app.main            # http://localhost:8099
python -m app.main --nu       # henter forslag med det samme
```

Docker: `docker compose up -d --build`

## Arkitektur

```
app/rema.py     henter og normaliserer tilbud
app/ai.py       to Claude-kald + validering
app/flow.py     ugens forløb, orkestrering
app/web.py      FastAPI: sider + JSON-endpoints
app/store.py    filbaseret state, én JSON-fil pr. uge
app/notify.py   valgfri Telegram-besked
app/main.py     uvicorn + APScheduler i samme proces
```

Der er ingen database. Al state er JSON i `data/`, skrevet atomisk via en
`.tmp`-fil og `replace()`. Det er bevidst — datamængden er ganske lille, og en
familie skal kunne kigge i filerne og rette i dem.

### Tilstandsmaskine

En uge går gennem `tom → arbejder → vaelger → arbejder → klar`, med `fejl` som
sidespor. Konstanterne ligger i `store.py`. Frontenden poller
`/api/uge/{noegle}/status` hvert 2. sekund mens status er `arbejder`, og
genindlæser når den skifter.

`flow._laas` (asyncio.Lock) forhindrer at to samtidige klik starter det samme
AI-kald to gange.

## Verificerede fakta om datakilden

`GET https://api.digital.rema1000.dk/api/v1/catalog/store/1/departments`

Ingen nøgle, ingen auth. Returnerer ~9 MB JSON: 15 afdelinger → kategorier →
varer. Testet 2026-08-29: 3.812 varer, hvoraf 212 med `is_on_discount`.

Vigtige felter under `pricing`:

- `is_on_discount` — reelt på tilbud. **Brug denne.**
- `is_advertised` — annonceret i avisen, men ofte samme pris som normalt.
  340 varer var annonceret, kun 212 reelt nedsat. Filtrér ikke på denne.
- `normal_price` / `price` — vi kræver `price < normal_price` som ekstra
  sikkerhed, hvilket luger yderligere ud (179 tilbage af 212).
- `price_changes_on` — dato hvor tilbuddet udløber.
- `price_per_unit` — fx "62.50 per Kg.", tekststreng.
- `max_quantity` — 0 betyder ingen grænse.

Varens `declaration` indeholder allergener i HTML. Vi strimler tags og
beskærer til 200 tegn.

**REMA's tilbud skifter om lørdagen**, så søndag morgen giver en frisk uge.

Endpointet er udokumenteret og kan ændre sig. `flow.hent_forslag()` afbryder
med en synlig fejl hvis der kommer under `MIN_TILBUD` varer retur, i stedet for
at bede Claude om at digte en madplan ud fra ingenting. Den fejlsikring må ikke
fjernes.

## AI-designet

**To kald, ikke ét.** Kald 1 foreslår ti retter. Kald 2 skriver opskrifter for
kun de valgte. At generere ti fulde opskrifter og smide fem væk er både dyrere
og giver dårligere resultat.

**Struktureret output via tool-use.** Begge kald bruger
`tool_choice: {"type": "tool", "name": ...}` med et JSON schema. Det er mere
pålideligt end at bede om JSON i prosa.

**Validering mod opdigtede tilbud.** Modellen skal returnere `tilbuds_ids` der
findes i input. `ai._valider_forslag()` kasserer retter med ukendte ID'er, og
`foreslaa_retter()` beder om erstatninger én gang hvis der bliver for få
tilbage. Uden det trin foreslår modellen før eller siden kylling til en pris
der ikke eksisterer. **Fjern ikke dette.**

Historikken i `data/historik.json` sendes med i prompten som en undgå-liste
plus optælling af hvad der ofte vælges og fravælges.

## Konventioner

- **Dansk i koden.** Funktions- og variabelnavne, kommentarer, commit-beskeder.
  `hent_tilbud`, `foreslaa_retter`, `uge_noegle`. Æ/ø/å undgås i identifiers
  (`foreslaa`, `vaelg`, `blaek`).
- **Ingen frontend-framework.** Server-renderet Jinja2 plus ~150 linjer vanilla
  JS. Ingen build-step. Hold det sådan.
- **Optimistisk UI.** Klik opdaterer med det samme og rulles tilbage hvis
  serveren afviser. Se `send()` i `app.js`.
- **Fejl skal være handlingsanvisende.** Ingen "noget gik galt" — sig hvad der
  skete og hvad man kan gøre.

### Designtokens

Ligger som CSS-variabler i toppen af `app.css`. Prisskiltet (`.skilt`,
`.mini-skilt`) er signaturelementet — gult, let roteret, monospace-tal. Blå
`--blaek: #12233d`, gul `--gul: #ffd23f`, køligt papir `--papir: #eef1f4`.
Valgt fordi det skal kunne læses i butikslys, ikke på en kontorskærm.

Skrifter hentes fra Google Fonts med systemfallback. Fejler CDN'et, ser det
stadig ordentligt ud.

## Faldgruber vi allerede er faldet i

- **`TemplateResponse` kræver `request` som første positionsargument** i nyere
  Starlette. Den gamle `TemplateResponse(navn, {"request": ...})` giver en
  kryptisk `TypeError: unhashable type: 'dict'`.
- **Nøgler til afkrydsning må ikke indeholde tekst fra data.** Første udgave
  brugte afdelingsnavnet, og `&` i "Frugt & grønt" blev HTML-escapet, så
  browserens nøgle ikke matchede serverens — fluebenene forsvandt ved
  genindlæsning. Nøglerne er nu rene indeks (`g0v1`).
- **`is_advertised` er ikke det samme som på tilbud.** Se ovenfor.

## Test

Der er ingen testsuite endnu. Sådan er der testet manuelt:

```bash
# Datalaget mod live-API
python -c "import asyncio; from app import rema; print(len(asyncio.run(rema.hent_tilbud())))"

# Hele flowet uden at bruge API-kvote: monkeypatch ai.foreslaa_retter
# og ai.lav_madplan med stubs der returnerer faste dicts.

# UI i rigtig browser
playwright: viewport 420x880, klik på .ret og .boks, reload, tjek persistens
```

**Første oplagte opgave:** lav pytest-dækning af `rema._normaliser`,
`ai._valider_forslag` og `store`-funktionerne. Det er de tre steder hvor en
regression ville være dyr og svær at opdage.

## Idéer der ikke er bygget

- Flere kæder (Netto og Lidl har tilsvarende endpoints) med sammenligning af
  hvor det samlede indkøb er billigst
- Portionsjustering pr. ret i UI'et
- "Har vi det hjemme?"-trin før indkøbslisten låses
- Eksport af opskrift til print
- Rester-håndtering: foreslå en ret der bruger det der bliver tilovers
