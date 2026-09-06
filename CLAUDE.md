# Madplan

Selvstændig Docker-container: henter REMA 1000's ugentlige tilbud, lader Claude
foreslå ti retter, familien vælger via et website, og der
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

**Næringskrav** (`kostregler.naering`) er hårde tærskler pr. portion:
`min_protein_g` og `maks_kalorier`. `ai._naeringskrav()` kasserer retter der
ikke lever op til dem, og kasserer også retter hvor tallet mangler — ellers
ville "glem at udfylde feltet" være vejen udenom kravet.

De ligger i en `naering:`-blok og ikke fladt som `maks_kalorier`, fordi den
generiske `maks_*`-løkke i `_regeltekst()` ellers ville rendere dem som
kategorilofter: "højst 700 retter af typen 'kalorier'".

**Tallene følger kravet, ikke retterne.** Målt 2026-09-01 med to tærskler:

```
min_protein_g: 50  ->  50 50 50 50 51 51 51 52 52 52 53 54 55 55
min_protein_g: 40  ->  40 40 40 41 41 41 42 42 42 42 43 43 44 44 45
```

Hele fordelingen flyttede sig ti gram ned sammen med tærsklen, og begge gange
ligger alle retter inden for fem gram lige over kravet. En linsegryde med
rodfrugter blev vurderet til 22 g ved det høje krav og 41 g ved det lave.

Så filteret er en **grov si mod det åbenlyst utilstrækkelige**, ikke en
næringsberegning. Det fanger de retter modellen selv indrømmer ligger langt
under, og det er værdien. Læs ikke et tal lige over grænsen som en måling, og
lad være med at bruge tallene til noget der kræver præcision.

For høj en tærskel koster i øvrigt bredde: målt ved 15 forslag gav 50 g kun
fjorten retter, ingen vegetarret og elleve danske kødretter, mens 40 g gav
femten, en vegetarret og fire køkkener. Samme mekanik gælder ved 10.

### Hvilken uge planlægges?

`store.planuge()` — **ikke** `uge_noegle()`. De to er ikke det samme, og
forskellen er en fejl vi har haft i drift.

`uge_noegle(d)` er ISO-ugen for en dato, rent og uden fortolkning.
`planuge(d)` er den uge madplanen *gælder for*: ISO-ugen for dagen efter.
Mandag til lørdag er det indeværende uge; kun søndag ruller den frem.

Grunden er at planen laves søndag morgen og gælder fra mandag. Med
`uge_noegle()` skrev søndagskørslen til den uge der sluttede samme dag, fandt
de forslag der allerede lå der fra ugens løb, og meldte "forslag findes
allerede" i stedet for at planlægge den kommende uge. Set søndag 2026-09-06:
kørslen kl. 8 ramte uge 36 i stedet for 37.

Alle steder der spørger "hvilken uge er det nu" skal bruge `planuge()`.
`uge_noegle()` med en eksplicit dato er stadig den rigtige til at oversætte en
dato til en nøgle — fx i historikken.

Uger gemt før rettelsen bærer den gamle nøgle. De er stadig læselige gennem
ugevælgeren; kun etiketten er forskudt én uge. Der er ikke lavet migrering,
for det er historik og ikke noget der skal regnes på.

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

`store.antal_valgt()` tæller begge dele — brug den, ikke `len(uge["valgt"])`.
Den ligger i `store` og ikke i `web`, fordi `flow.paamindelse()` også skal
bruge den, og `web` importerer `flow` — ikke omvendt.

### Ugedag og ugens pris

Hver ret kan tildeles en aften: `forslag[i]["dag"]` og `egne[i]["dag"]`, tom
streng eller et navn fra `web.DAGE`. Valget er **manuelt** — AI-kaldene ved
intet om dage.

Opskrifterne fra kald 2 kender ikke dagen; `web._med_dage()` kobler den på via
rettens navn og sorterer efter ugens rækkefølge, med retter uden dag sidst.
Matcher et navn ikke — modellen kan have omformuleret det — ryger retten
bagerst uden dag frem for at forsvinde.

`web._totaler()` giver `antal`, `pris` og `spar` og returneres fra alle
endpoints der ændrer valg eller portioner, så bundbjælken kan opdateres uden
at genindlæse. **Prisen er et skøn**: `pris_pr_portion` er modellens vurdering,
ikke en beregning ud fra tilbudspriserne — deraf "ca." i skabelonen. `spar` er
derimod rigtige tal fra REMA.

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

`push.send()` siger til når forslagene er klar, når madplanen er skrevet, og
**når noget går galt**. `flow._fejl()` sender fejlbeskeden videre til
telefonerne — uden det ville en fejlet søndagskørsel stå og vente på at nogen
tilfældigt åbnede websitet, og hele pointen med den automatiske kørsel er at
ingen skal holde øje.

Derfor er `_fejl()` async, og derfor ligger kaldet i `lav_madplan()` **uden for**
`_laas`: et netværkskald må ikke holde AI-låsen i op til tyve sekunder.

`flow.paamindelse()` kører kl. 17 (`PAAMINDELSE_CRON`) og puffer til familien
hvis ingen har valgt endnu. Har nogen valgt — også hvis det bare er en egen ret
— siger den ingenting; en påmindelse man ikke skal handle på er støj.

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
  105  og ikke fanget af UDELUK
   93  og ikke en færdigsuppe      ← det er dem AI'en ser
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

`rema.UDELUK_NAVN` fjerner færdigsupper. Den er adskilt fra `UDELUK`, fordi
den **kun** må se på varenavnet: REMA's suppe-kategori rummer også suppeurter
og kødboller, som er ingredienser til rigtig mad. Mønsteret rammer ord der
slutter på `-suppe`, så `suppehøne` og `suppeurter` bliver stående. Målt
2026-08-30 fjernede den 12 varer; matchede den også på kategorien, ville den
have taget 15 og dermed tre brugbare ingredienser med.

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
`foreslaa_retter()` beder om erstatninger op til `FORSOEG_FORSLAG` gange hvis
der bliver for få tilbage. To runder, ikke én: lofterne på 1 gør at den
første runde ikke altid rækker. Runden fortæller modellen præcist hvor mange der
mangler, hvor mange af dem der skal bruge tilbud, og hvilke kategorier der er
fyldte — og afbryder hvis en runde ikke tilføjer noget, så vi ikke betaler for
et kald der ikke rykker. Uden det trin foreslår modellen før eller siden kylling til en pris
der ikke eksisterer. **Fjern ikke dette.**

**Forslagene er todelte.** Mindst `MIN_MED_TILBUD` (5) af de
`ANTAL_FORSLAG` (10) retter skal bygge på ugens tilbud; resten er sæsonretter
med tom `tilbuds_ids`. Det er ikke dovenskab: med ~93 brugbare tilbud, og efter
kostregler, fravalg, tilbudsspredning og suppefilter, er der sjældent nok
fornuftige retter i én uges tilbud til at fylde det hele. Tvinger man dem
igennem, bliver de sidste til fyld.

Tallene var 15 og 8, men så mange retter kunne ikke overholde alle
kostreglerne på én gang — lofterne på 1 er de samme uanset hvor mange retter
der skal findes, så de bider hårdere jo flere man beder om.

**Det er en fordeling, ikke et gulv.** `_fordel_tilbud()` sætter loft i begge
retninger. Med kun et gulv byggede modellen alle ti retter på tilbud, fordi
det er den nemmeste vej, og sæsonretterne forsvandt helt — målt 2026-09-03.

To ting følger af fordelingen, og de er værd at kende før nogen "retter" dem:

- **Vi lander typisk på 9-10, ikke altid 10.** Sæsonhalvdelen kan ikke altid
  fylde fem under proteinkravet: dansk efterårsmad er grøntsagstung, og
  grønlangkål med medister (26 g) og grønkålssuppe (24 g) blev begge kasseret.
- **Regel B siger bevidst ikke "dansk".** Det gjorde den, og så blev
  halvdelen af ugen dansk per instruks. Målt tre kørsler hver vej:

  | | Med "dansk" | Uden |
  |---|---|---|
  | danske retter | 25 af 28 (89 %) | 16 af 29 (55 %) |
  | vegetarret | 1 af 3 kørsler | 3 af 3 |
  | køkkener pr. uge | 1-2 | 4-5 |

  Sæsonretterne er stadig genkendelig hverdagsmad — boeuf bourguignon, pasta
  med hakket oksekød, bagte grøntsager med linser. Værnet mod restaurantmad
  ligger i systemprompten og i "ikke fyld: retter familien ville lave
  alligevel", ikke i ordet dansk. Sæt det ikke tilbage.

`_valider_forslag()` kræver derfor **ikke længere** mindst ét tilbud pr. ret —
den linje var hele spærren. Ukendte ID'er kasseres stadig; det er den
validering der betyder noget. Gulvet håndhæves i stedet som et loft fra den
anden side af `_maks_uden_tilbud()`: højst `ANTAL_FORSLAG - MIN_MED_TILBUD`
retter uden tilbud.

Årstiden kommer fra `_saeson()`, der læser måneden af `store.nu()`. Kun måned
og årstid sendes med — der er bevidst ingen liste over sæsonråvarer at
vedligeholde.

**Gentagelser fanges i koden.** `_fjern_gentagelser()` kasserer retter hvis
navn ligner noget fra de sidste `UNDGAA_UGER` (4) uger. Navne er fri tekst, så
præcis sammenligning fanger ikke "Kyllingegryde med champignon" mod "med
svampe" — derfor `difflib` med grænsen `GENTAGELSE_GRAENSE` (0,72). Målt på
rigtige navne: identiske 1,00, omskrivninger 0,77-0,94, en helt anden ret langt
under. Det er en heuristik, og **hver frasortering logges med lighedstallet**,
så grænsen kan kalibreres uden at gætte.

Rækkefølgen i `_rens()` er ikke tilfældig: gentagelser fjernes *før* lofterne,
så en kasseret gentagelse ikke når at optage pladsen i et loft.

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

**Rester noteres i kald 2.** `rester` i `VAERKTOEJ_MADPLAN` er en liste af
`{vare, forslag}` — hvad der bliver reelt tilovers fordi varen sælges i større
enheder end retten bruger, og hvad det kan bruges til. Feltet er **ikke**
`required`, så uger gemt før det fandtes stadig renderer.

Retternes dag sendes med i prompten netop for det: så kan noten pege på
"grønkålssalaten mandag" i stedet for at være generisk. Verificeret 2026-08-30.

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

**`<select>` skal have `appearance: none`.** WebKit ignorerer `background`,
`border-radius` og det meste andet på en select medmindre den native styling
slås fra — så ser den ustylet ud på Safari, altså på telefonerne. Til gengæld
forsvinder den native pil, så `.dag-vaelger` og `.ugevaelger` tegner deres egen
som en data-URI. Farverne står som literaler derinde; CSS-variabler virker ikke
i en data-URI.

**CSS og JS hænges op med `?v=`.** `web._statisk_version()` bruger filernes
mtime, så en opdatering giver et nyt link. Uden det serveres de kun med ETag,
og browseren må selv gætte hvor længe den holder på dem — en designrettelse kan
være usynlig indtil nogen tømmer cachen, og på en telefon med siden på
hjemmeskærmen er det ikke ligetil.

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
- **Compose-filen på NAS'en er en manuel kopi.** Watchtower opdaterer imaget,
  ikke compose-filen, så den driver fra repoet indtil nogen henter den ned.
  Ændrer man en standard i compose-filen — fx `ANTAL_FORSLAG:-15` — sker der
  ingenting på NAS'en før filen er hentet igen. Og ændringer i `.env` kræver
  et `up -d`, fordi miljøet bages ind ved oprettelsen og Watchtowers
  genskabelse kopierer den gamle konfiguration med.
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
- **Alle skrivninger af uge-filen går gennem `web._opdater()`.** Den holder
  `_skrivelaas` hen over læs-ret-skriv. Skriv aldrig `store.gem_uge()` direkte
  i et endpoint.

  Bemærk *hvorfor*, for den oprindelige begrundelse her var forkert. Racen var
  ikke "et vindue på millisekunder": endpointsene er `async def` uden `await`
  mellem læsning og skrivning, så event-loopet kan slet ikke skifte midt i, og
  vinduet var **nul**. Det holder kun så længe ingen indsætter et `await` i den
  blok. Gør nogen det — en push-besked, et opslag, hvad som helst — taber to
  samtidige klik den enes skrivning, tavst. Målt begge veje; se
  `test_ingen_skrivninger_tabes`, som fejler hvis låsen fjernes.

  Afviser ændringsfunktionen med en `JSONResponse`, gemmes ugen ikke. Så
  efterlader en 400 eller 409 ikke en halvt ændret uge på disken.

  `flow._laas` er bevidst en anden lås: den holdes hen over AI-kaldene i op til
  halvandet minut, og genbrug ville fryse hele websitet imens.

## Test

```bash
pip install -r requirements-dev.txt
pytest -q
```

115 tests, ingen netværkskald, ingen brug af den rigtige `data/`-mappe —
`conftest.py` peger `config.DATA_DIR` på en `tmp_path` pr. test.

Testene er ikke pyntearbejde. **Hvert filter i `ai.py` koder en fejl vi har
ramt i produktion**, og testen navngiver hvilken: det dobbeltkodede JSON-svar,
strengen midt i retterlisten, bladselleri der slap gennem fravalgslisten,
spidskål tre aftener på en uge, gullaschsuppen hæftet på hakket kød,
basisvarer der endte på indkøbslisten. Fjerner nogen et af de værn for at
forenkle, falder en test med en besked om hvorfor det var der.

| Fil | Dækker |
|---|---|
| `test_ai.py` | Filtrene og håndhævelsen — lofter, spredning, fravalg, gentagelser, basisvarer, sæson |
| `test_kald.py` | Gentagelse ved 429 og 5xx mod en lokal server, og at 4xx **ikke** gentages |
| `test_rema.py` | Normalisering, `UDELUK`, `UDELUK_NAVN`, prompt-linjer |
| `test_store.py` | Ugefiler, atomisk skrivning, historik, abonnementer |
| `test_web.py` | Endpoints gennem `TestClient` — grænser, 400, 409, egne retter |

Async-funktioner køres med `asyncio.run()` i testkroppen; der er bevidst ingen
`pytest-asyncio`, så udviklingsafhængigheden er ét enkelt pakke.

`pytest` kører i GitHub Actions som `needs` for docker-jobbet, så et brækket
filter ikke kan nå NAS'en. `tests/` og `requirements-dev.txt` er i
`.dockerignore` — imaget skal ikke bære testværktøj.

Ting der stadig kun er afprøvet i hånden:

```bash
# Datalaget mod live-API
python -c "import asyncio; from app import rema; print(len(asyncio.run(rema.hent_tilbud())))"

# UI i rigtig browser
playwright: viewport 420x880, klik på .ret og .boks, reload, tjek persistens
```

## Idéer der ikke er bygget

- Flere kæder (Netto og Lidl har tilsvarende endpoints) med sammenligning af
  hvor det samlede indkøb er billigst
- Portionsjustering pr. ret i UI'et
- "Har vi det hjemme?"-trin før indkøbslisten låses
- Eksport af opskrift til print
