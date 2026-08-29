# Madplan

En selvstændig Docker-container der hver søndag henter REMA 1000's tilbud, får
Claude til at foreslå ti retter bygget på dem, og lader familien vælge fra hver
sin telefon. Når I har valgt, skriver den opskrifter og en samlet indkøbsliste
I kan krydse af mens I handler.

Ingen Home Assistant, ingen apps. Bare et website på din NAS.

## Ugens forløb

| Tidspunkt | Hvad der sker |
|---|---|
| Søndag 08:00 | Henter tilbud og laver 10 forslag |
| Løbende | Familien åbner websitet og trykker på de retter de vil have |
| Når nogen trykker **Lav madplanen** | Opskrifter og indkøbsliste bliver skrevet |
| Søndag 18:00 | Har ingen trykket, tages de fem første forslag automatisk |

## Kom i gang

```bash
cp .env.example .env
nano .env                        # ANTHROPIC_API_KEY er det eneste påkrævede
nano config/praeferencer.yaml    # tilpas til jeres husstand
docker compose up -d --build
```

Åbn `http://<NAS-IP>:8099`. Tryk **Hent ugens forslag** hvis du ikke vil vente
til søndag.

På telefonen: Del → Føj til hjemmeskærm. Så ligger den som et ikon og åbner
uden browserlinje.

## Konfiguration

Alt i `.env` bortset fra API-nøglen er valgfrit.

| Variabel | Standard | Hvad den gør |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Påkrævet |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Modellen der bruges |
| `WEB_PORT` | `8099` | Port websitet lytter på |
| `FORSLAG_CRON` | `sun 8` | Hvornår forslagene hentes |
| `DEADLINE_CRON` | `sun 18` | Hvornår ugen lukkes automatisk |
| `ANTAL_FORSLAG` | `10` | Antal retter der foreslås |
| `ANTAL_RETTER` | `5` | Antal der vælges hvis ingen har stemt |
| `MAD_AFDELINGER` | `10,20,…` | Hvilke REMA-afdelinger der kigges i |
| `BASE_URL` | — | Adressen der linkes til i Telegram-beskeden |
| `TELEGRAM_TOKEN` | tom | Valgfri notifikation. Tom = slået fra |

`config/praeferencer.yaml` er det vigtigste at rette. Det er her forslagene går
fra generiske til jeres: antal personer, allergier, hvad I ikke gider, hvor
lang tid I har på en hverdag, og hvad I altid har hjemme.

### Telegram (valgfrit)

Uden det virker alt — I skal bare selv huske at åbne websitet om søndagen.
Med det får I et praj med et link når forslagene er klar.

Skriv til [@BotFather](https://t.me/BotFather), send `/newbot`, gem tokenet.
Opret en gruppe, tilføj botten, send en besked, og hent chat-ID'et:

```bash
curl https://api.telegram.org/bot<DIT_TOKEN>/getUpdates
```

## Sådan hænger det sammen

```
app/rema.py     henter tilbud fra REMA's katalog-API
app/ai.py       de to Claude-kald + validering mod opdigtede tilbud
app/flow.py     ugens forløb
app/web.py      websitet og JSON-endpoints
app/store.py    filbaseret state, én JSON-fil pr. uge
app/notify.py   valgfri Telegram-besked
data/           ugefiler og historik.json
```

**Hvorfor to AI-kald?** Det første foreslår ti retter ud fra tilbuddene. Det
andet skriver kun opskrifter for dem I faktisk valgte. At generere ti fulde
opskrifter og smide fem væk er både dyrere og dårligere.

**Hvorfor validering?** Modellen skal referere til konkrete tilbuds-ID'er, og
retter der peger på varer som ikke findes i ugens tilbud bliver kasseret og
erstattet. Uden det trin foreslår den før eller siden kylling til en pris der
ikke eksisterer.

**Historikken** i `data/historik.json` husker hvad I valgte og fravalgte, og
sendes med i næste uges prompt. Forslagene bliver mærkbart bedre efter et par
måneder.

## Datakilden

`api.digital.rema1000.dk` — det katalog-API REMA-appen selv bruger. Ingen nøgle
nødvendig. Hver vare har normalpris, tilbudspris, kilopris, maks-antal,
varedeklaration og en udløbsdato.

Det er ikke et dokumenteret API, så det kan ændre sig. Derfor tjekker systemet
at der kommer mindst `MIN_TILBUD` varer retur; er der færre, vises en fejl på
forsiden i stedet for at bede Claude om at digte en madplan.

REMA's tilbud skifter om lørdagen, så søndag morgen giver en frisk uge.

## Drift

```bash
docker compose logs -f              # se hvad den laver
curl localhost:8099/sundhedstjek    # er den i live
```

Data ligger som almindelig JSON i `data/`. Vil du starte forfra, så slet
`data/uge-*.json`. Historikken ligger separat i `data/historik.json`.

To Claude-kald om ugen koster nogle få kroner om måneden.
