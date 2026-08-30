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

Docker lokalt: `docker compose up -d --build` (bygger fra kildekoden).

På NAS'en bruges `docker-compose.synology.yml` i stedet — den *henter* imaget
fra GHCR frem for at bygge det. De to compose-filer er med vilje adskilt: den
ene er til at udvikle i, den anden til at køre i drift.

**Kræver Python 3.10 eller nyere.** Containeren bruger 3.12, men lokalt rammer
man let systemets python — på macOS er den 3.9, og så dør opstarten. Årsagen er
`dict | None` i signaturen på `web.start_forslag()`: `from __future__ import
annotations` gør den til en streng, men FastAPI evaluerer den alligevel med
`get_type_hints()`, og `|` mellem typer findes først i 3.10.

## Arkitektur

```
app/rema.py     henter og normaliserer tilbud
app/ai.py       to Claude-kald + validering
app/flow.py     ugens forløb, orkestrering
app/web.py      FastAPI: sider + JSON-endpoints
app/store.py    filbaseret state, én JSON-fil pr. uge
app/push.py     valgfri Web Push til telefonerne
app/main.py     uvicorn + APScheduler i samme proces
app/config.py   miljøvariabler + indlæsning af praeferencer.yaml

config/praeferencer.yaml   husstandens smag — se nedenfor
```

Der er ingen database. Al state er JSON i `data/`, skrevet atomisk via en
`.tmp`-fil og `replace()`. Det er bevidst — datamængden er ganske lille, og en
familie skal kunne kigge i filerne og rette i dem.

### Præferencefilen

`config/praeferencer.yaml` er det sted man tuner systemet — ikke prompterne i
`ai.py`. Hele filen sendes med som JSON i **begge** AI-kald
(`ai._praeferencetekst`), og `antal_personer` styrer portionsstørrelsen i kald
2. Skal forslagene ændre karakter (mere vegetarisk, kortere hverdage, en vare
familien ikke gider), er det her man retter — ikke i systemprompten.

Filen er valgfri: mangler den, kører alt videre med "Ingen særlige præferencer
angivet."

`standard_portioner` er kun **startværdien** hvert forslag får. Det rigtige
antal vælges pr. ret i websitet, så en uge kan tage højde for hvem der er
hjemme. Feltet hed tidligere `antal_personer` og gjaldt hele ugen.

**Kostregler** (`kostregler:`) styrer hvilke slags retter der må foreslås, og
findes i to slags:

- `profil:` — fri tekst (fx "proteinrige retter"). Sendes ordret til modellen
  under overskriften "Krav til retterne". Rent kvalitativt; kan ikke måles.
- `maks_<mærkat>` — talmæssige lofter. De sendes også til modellen, men
  **håndhæves derudover i koden** af `ai._haandhaev_lofter()`. Det er
  nødvendigt: modellen overholder ikke et "højst én" pålideligt.

`ai._maerkater()` giver hver ret de mærkater den tæller med i. Der er tre
kilder, og en ret kan bære flere — en dyr thairet er både `asiatisk` og `dyre`.
Rammer den bare ét fyldt loft, ryger den:

| Kilde | Mærkater | Kommer fra |
|---|---|---|
| kategori | `koed`, `fisk`, `vegetar` | skemaets enum |
| køkken | `dansk`, `italiensk`, `asiatisk`, `mexicansk`, `mellemoestlig`, `andet` | skemaets enum |
| pris | `dyre` | `pris_pr_portion` > `dyr_over_kr` |

Håndhævelsen er generisk over `maks_*`, så `maks_mexicansk: 2` i YAML'en virker
uden kodeændring. Rækkefølgen bevares, så det er de senere retter i en
overfyldt gruppe der ryger. En ny **kategori eller et nyt køkken** kræver
derimod både en linje i `ai.KATEGORIER`/`ai.KOEKKENER` og i skemaets `enum`.

Bemærk at `dyre` hviler på modellens eget prisskøn i `pris_pr_portion`. Det er
et skøn, ikke en beregning ud fra tilbudspriserne.

### Tilstandsmaskine

En uge går gennem `tom → arbejder → vaelger → arbejder → klar`, med `fejl` som
sidespor. Konstanterne ligger i `store.py`. Frontenden poller
`/api/uge/{noegle}/status` hvert 2. sekund mens status er `arbejder`, og
genindlæser når den skifter.

`flow._laas` (asyncio.Lock) forhindrer at to samtidige klik starter det samme
AI-kald to gange — men mekanismen er ikke låsen alene. Låsen slippes *inden*
AI-kaldet går i gang; det der faktisk værner, er at `status = ARBEJDER` skrives
til disk inde i låsen, og at tjekket øverst i `hent_forslag()` og
`lav_madplan()` læser den igen. Flytter man den skrivning uden for låsen,
forsvinder beskyttelsen — også selvom låsen stadig står der.

### Portioner og egne retter

Hver ret bærer sit eget `portioner`-tal, både forslagene og familiens egne.
`ai.lav_madplan()` skriver antallet ind pr. ret i prompten og beder om at
mængderne skaleres derefter; indkøbslisten dækker summen. Verificeret
2026-08-29 med 2, 6 og 3 personer på tre retter.

Familiens egne ønsker ligger i `uge["egne"]` som
`{navn, portioner, valgt}`. De er **bevidst holdt uden for** `forslag`:

- `valgt` er indeks i `forslag`, og `hent_forslag()` skriver den liste om.
  Egne retter overlever derfor at man henter nye forslag — de har deres eget
  `valgt`-flag i stedet for at ligge i indeksrummet.
- De har ingen `tilbuds_ids`. `ai.lav_madplan()` bruger derfor
  `ret.get("tilbuds_ids") or []` og markerer dem som familiens eget ønske i
  prompten, så modellen ikke leder efter tilbud der ikke findes.

`web._antal_valgt()` tæller begge dele — brug den, ikke `len(uge["valgt"])`.

### Udrulning

`.github/workflows/deploy.yml` bygger og pusher `ghcr.io/<ejer>/madplan` ved
hvert push til `main`, for `linux/amd64` og `linux/arm64` — NAS'er er begge
dele afhængigt af model. Tags: `latest` og den korte commit-sha.

På NAS'en kører en Watchtower med `--label-enable`, så **kun** containere med
`com.centurylinklabs.watchtower.enable=true` opdateres. Det er bevidst: uden
det flag ville den også opdatere alt andet på NAS'en.

Tre ting må ikke havne i imaget, og `.dockerignore` holder dem ude:
`.env` (nøglen), `data/` (ugerne) og `.git`. `data/` og `config/` er volumes,
så state og præferencer overlever en opdatering.

`cloudflared` i samme compose-fil giver HTTPS udefra uden åbne porte —
forbindelsen er udgående. Den peger på `madplan:8099` over Docker-netværket,
ikke på NAS'ens IP. Det er ikke pynt: push virker kun i sikker kontekst.
Appen har intet login, så noget som Cloudflare Access hører til foran den.

Dockerfilen har et `HEALTHCHECK` mod `/sundhedstjek`. Det bruger stdlib i
stedet for `curl`, så imaget ikke skal vokse med en pakke mere.

### Push-beskeder

`push.send()` siger til når forslagene er klar, og når madplanen er skrevet.
Den er valgfri og gør ingenting hvis VAPID-nøglerne er tomme. Der var
tidligere også en Telegram-kanal; den er fjernet, fordi push dækker behovet
uden at kræve en konto.

**Push kræver HTTPS.** Service workers og Push API'et findes ikke i browseren
uden for sikker kontekst. På `http://<NAS-IP>:8099` er `navigator.serviceWorker`
`undefined`, og `app.js` skjuler hele afsnittet frem for at love noget den ikke
kan holde. Kun `localhost` regnes også for sikker.

**`sw.js` serveres fra roden, ikke fra `/static/`.** En service workers scope
er den mappe den ligger i — fra `/static/sw.js` ville den aldrig se resten af
websitet. Derfor er der en rute i `web.py` der leverer filen på `/sw.js`.

Workeren cacher **ikke** noget. Det er bevidst: siden er server-renderet, og en
cache ville betyde at nogen kunne stå i butikken med en gammel indkøbsliste.

`store.abonnementer.json` har én post pr. browser, ikke pr. person — samme
telefon i to browsere er to abonnementer. Endpoint'et er identiteten. Svarer
push-tjenesten 404 eller 410, er abonnementet dødt (telefon nulstillet, app
fjernet, beskeder slået fra), og `push.send()` rydder det væk selv.

`LEVETID` sætter TTL på beskeden. Standarden i protokollen er 0, altså "lever
nu eller smid væk" — og så ville en telefon der lå slukket søndag morgen aldrig
få beskeden.

VAPID-nøglerne laves med `python -m app.push`. Den private er en hemmelighed og
hører i `.env`; skifter man den, skal alle abonnenter tilmelde sig igen.

## Verificerede fakta om datakilden

`GET https://api.digital.rema1000.dk/api/v1/catalog/store/1/departments`

Ingen nøgle, ingen auth. Returnerer ~9 MB JSON: 15 afdelinger → kategorier →
varer. Målt 2026-08-29: 3.748 varer, hvoraf 212 med `is_on_discount`.

Hele sien, samme måling — det er det sidste tal `MIN_TILBUD` vurderes imod:

```
3.748  varer i alt
  212  is_on_discount
  179  og reelt billigere (price < normal_price)
  123  og i MAD_AFDELINGER
  105  og ikke fanget af UDELUK   ← det er dem AI'en ser
```

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

`rema.UDELUK` sorterer varer fra som ligger i madafdelingerne, men ikke hører
hjemme i en madplan: slik, sodavand, alkohol, dyrefoder, vaskepulver. Vær
opmærksom på `\bis\b` — den fjerner is, men rammer også alt andet hvor "is"
står som selvstændigt ord.

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

**Kun de 140 bedste tilbud når frem til modellen.** `rema.til_prompt_linjer()`
beskærer til `maks=140`, og listen er sorteret efter rabatprocent, så det er de
dårligste tilbud der ryger. Med de ~180 varer vi typisk ser, betyder det at en
håndfuld aldrig kommer i prompten. Det forklarer hvorfor modellen kan "overse"
en billig vare der står i `data/uge-*.json`.

**Ingen SDK.** API'et kaldes råt over `httpx` — samme klient som REMA-kaldet,
ingen ekstra afhængighed. Installér ikke `anthropic`-pakken for at gøre
`ai._kald()` pænere. Standardmodellen er `claude-sonnet-5`, sat i `config.py`
og overskrivelig med `ANTHROPIC_MODEL`. To kald om ugen koster i
størrelsesordenen 30 kr. om året; `claude-opus-5` er ~2,5× det.

**Forbigående fejl gentages.** `ai._kald()` prøver op til `FORSOEG` gange med
voksende pause ved 429 og 5xx samt netværks- og timeoutfejl, og respekterer
`retry-after`. 4xx gentages **ikke** — en forkert nøgle bliver ikke rigtig af
at spørge igen. `SAMLET_FRIST` sætter loft over hvor længe der samlet prøves,
så websitet ikke står med en spinner i et kvarter.

Det er ikke pyntearbejde: den ugentlige kørsel er søndag kl. 8, og går den i
fejl, står madplanen tom til nogen opdager det. Vi ramte 2026-08-29 en 503
"credential validation failed" hvor nøglen var helt i orden.

**Fejl oversættes.** `ai._fejlbesked()` graver Anthropic's egen besked ud og
oversætter de almindelige tilfælde (ugyldig nøgle, tom konto, hastighedsgrænse)
til dansk. Brug ikke `raise_for_status()` her — den giver kun
`Client error '401 Unauthorized' for url ...`, som ingen kan handle på.

**Samme tilbud må ikke bære for mange retter.** `maks_gentaget_tilbud` i
`kostregler` sætter loftet, og `ai._spred_tilbud()` håndhæver det. Uden det
bygger modellen gerne tre af ti aftener på den samme spidskål, fordi den er
billig. Variationsreglen i prompten dækker også grøntsager og tilbehør, ikke
kun proteinkilder.

**Fravalg håndhæves i koden.** `ai._fjern_uoenskede()` kasserer retter hvor
`allergier` eller `kan_vi_ikke_lide` optræder i rettens navn, beskrivelse
eller i navnet på et af de tilbud den bygger på. Prompten beder også om det,
men modellen foreslog bladselleri to uger i træk selvom det stod på listen.

Mønsteret matcher **delstreng**, ikke hele ord — modsat basisvarefilteret.
'selleri' skal også fange 'bladselleri'. Afvejningen er en anden: en falsk
positiv koster ét forslag ud af ti, mens en forbier sætter noget på bordet
familien har sagt fra til.

**Regel 1 kræver ikke længere 2-4 tilbud pr. ret.** Det pressede modellen til
at hæfte urelaterede tilbud sammen — "frikadeller med suppe" var resultatet.
Nu er kravet mindst ét tilbud, gerne to-tre, og kun varer der hører sammen i
retten. Regel 1b siger det ligeud med det eksempel. Skruer man kravet op igen,
kommer de mærkelige kombinationer tilbage.

**Indkøbslisten luges bagefter.** `ai._fjern_basisvarer()` fjerner varer fra
`har_altid_hjemme` som modellen alligevel skrev på listen. Prompten beder om
det, men målt 2026-08-29 slap 3 ud af 19 varer igennem. Mønsteret matcher korte
ord kun som hele ord (`mel` må ikke fange `melon`) og længere ord også som
forstavelse (`pasta` fanger `pastaskruer`), med flertals-`er` trimmet af
stammen. Samme mønster som kostreglerne: bed om det i prompten, ryd op bagefter.

Historikken i `data/historik.json` sendes med i prompten som en undgå-liste
plus optælling af hvad der ofte vælges og fravælges.

## Konventioner

- **Dansk i koden.** Funktions- og variabelnavne, kommentarer, commit-beskeder.
  `hent_tilbud`, `foreslaa_retter`, `uge_noegle`. Æ/ø/å undgås i identifiers
  (`foreslaa`, `vaelg`, `blaek`).
- **Ingen frontend-framework.** Server-renderet Jinja2 plus ~150 linjer vanilla
  JS. Ingen build-step. Hold det sådan.
- **Optimistisk UI.** Klik opdaterer med det samme og rulles tilbage hvis
  serveren afviser. Se `send()` i `app.js`. Undtagelsen er at tilføje og
  fjerne egne retter: de genindlæser siden, fordi en sletning omnummererer
  de øvrige egne retter, og at rette indeks i klienten ville være en fejlkilde.
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
- **`docker-compose.synology.yml` opremser miljøvariablerne én for én.** Den
  bruger ikke `env_file`, fordi det ikke virkede på DSM's Compose v1. Prisen
  er at listen skal holdes synkroniseret: tilføjer man noget i `config.py`
  uden at føje det til compose-filen, når det aldrig ind i containeren, og
  funktionen ser bare ud til ikke at virke. Det skete for VAPID-nøglerne.
- **Ret-kortet er `<li class="ret-kort">`, ikke knappen.** Det var det
  oprindeligt, men portionsvælgeren skal ligge i kortet, og en `<button>` inde
  i en `<button>` er ugyldig HTML som browseren river fra hinanden. Kortets
  udseende og `er-valgt` hænger derfor på `<li>`, mens `.ret` kun er den
  klikbare del. `app.js` skifter klasse via `knap.closest(".ret-kort")`.
- **Modellen dobbeltkoder af og til hele svaret som en JSON-streng.** Set i
  praksis 2026-08-29: `input` var `{"retter": "{\"retter\": [...]}"}` i stedet
  for et objekt. Uden `ai._udpak_retter()` løber valideringen hen over strengen
  tegn for tegn, logger 250 advarsler og kasserer alt — og det oprindelige
  symptom var en uforståelig `AttributeError`. Indholdet er gyldigt, så vi
  pakker ud frem for at smide et betalt kald væk. Fjern ikke det trin.
- **Livscyklussen hægtes på appen i `main.py`, ikke i `FastAPI()`-kaldet.**
  Scheduleren hører til i `main.py`, men appen bliver født i `web.py`, så
  `main()` sætter `app.router.lifespan_context` før `uvicorn.run`. Flyt det
  ikke til `web.py` for at gøre det "rigtigt" — så blander web-laget sig med
  planlægningen. Al opstart går gennem `python -m app.main`, også i Docker.
- **`vaelg` og `kryds` er læs-ret-skriv uden lås.** Begge endpoints læser hele
  uge-dicten, retter ét felt og skriver alt tilbage. Klikker to familiemedlemmer
  samtidig fra hver sin telefon, kan den ene skrivning overskrive den anden.
  Vinduet er millisekunder, så vi har ikke set det i praksis — men det er
  præcis det scenarie appen er bygget til, så udvid ikke mønstret til flere
  endpoints uden at tage en lås med.

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
