# Lausuntovahti

Viikoittainen seuranta kuulutuksista, joihin voi vastata: Vantaanjoen, Espoonjoen ja Mankinjoen valuma-alueiden kunnat (16 kpl), Lupa- ja valvontavirasto, elinvoimakeskukset, Väylävirasto, maakuntien liitot, lausuntopalvelu.fi. Tulos on Outlookiin tilattava kalenteri, jossa jokainen määräpäivä on oma tapahtumansa.

## Miten se toimii

1. Maanantaisin n. klo 12 (Helsinki) Claude-ajo lukee `config/sources.json`-lähteet ja poimii uudet kuulutukset JSON-muotoon (`incoming/<pvm>.json`). Ohje: `TASK_PROMPT.md`.
2. `scripts/build_calendar.py` yhdistää poiminnan tilaan (`state/items.json`), pisteyttää kohteet sääntöjen mukaan (`config/keywords.json`), kirjoittaa `calendar.ics`-tiedoston ja viikkoyhteenvedon (`docs/summary-<pvm>.md`).
3. Muutokset commitataan tähän repoon. Outlook hakee `calendar.ics`-tiedoston raw-osoitteesta.
4. Ajo lähettää lyhyen yhteenvedon: uudet high/medium-kohteet, lähestyvät määräpäivät, lähteet joiden luku epäonnistui.

Vain ihmiselle tarkoitettu osa: lausuntojen kirjoittaminen. Vahti ei lähetä mitään mihinkään.

## Kalenterin tilaaminen Outlookiin

Raw-osoite: `https://raw.githubusercontent.com/antti-a/lausuntovahti/main/calendar.ics`

- **Outlook (uusi / web):** Kalenteri → Lisää kalenteri → Tilaa verkosta → liitä osoite → nimi "Lausuntovahti".
- **Outlook (klassinen työpöytä):** Kalenteri → Avaa kalenteri → Internetistä → liitä osoite.
- Jaa osoite muille yhdistyksen jäsenille; kukin tilaa sen itse. Outlook päivittää tilatut kalenterit tyypillisesti kerran vuorokaudessa, joten maanantain ajo näkyy viimeistään tiistaina.

Tapahtumat:

- `<otsikko> (kunta) – DL` määräpäivänä.
- `<otsikko> (kunta) – uusi, DL 20.11.` sinä maanantaina, jona kohde löytyi – jotta uusi kohde näkyy kuluvalla viikolla eikä vasta määräpäivänä.
- `<otsikko> (kunta) – DL ?`, jos määräpäivää ei vielä tiedetä. Tapahtuma siirtyy joka maanantai kuluvalle viikolle, kunnes määräpäivä selviää.

Prioriteetti (high/medium) ja toimenpide (mielipide, muistutus, lausunto, vastine) ovat tapahtuman kuvauksessa ja luokassa (category).

`(arvio)` tarkoittaa, että määräpäivä on päätelty julkaisupäivästä (+30 pv) eikä luettu kuulutuksesta – tarkista linkistä.

## Mitä kalenteriin menee

Pisteytys (`config/keywords.json`):

| Tekijä | Pisteet |
|---|---|
| Vesiaiheinen avainsana otsikossa tai tyypissä | +3 |
| … tai vain tiivistelmässä (tiivistelmä kirjoitetaan aina vesinäkökulmasta) | +1 |
| Suuri suunnitelma (yleiskaava, maakuntakaava, YVA, ympäristölupa, rata/tie…) | +2 |
| Toimenpide mielipide/muistutus/lausunto/vastine | +1 |
| Seurattava kunta | +1 (vaatimus, paitsi alueelliset tyypit) |

≥4 = **high**, ≥2 = **medium** → kalenteriin. Alle 2 = low → vain yhteenvetoon. Päätökset ja tiedoksiannot (ei vastausmahdollisuutta) ovat aina low. Poissulkulista pudottaa rallit, triathlonit, laajakaistat ja virat.

Säätäminen:
- Liian paljon tavaraa → nosta `thresholds.medium` arvoon 3 tai poista avainsanoja.
- Liian vähän → laske kynnyksiä tai lisää avainsanoja.
- Avainsana osuu sanan alkuun, ja perässä saa olla enintään 5 kirjainta taivutusta (`vesistö` → "vesistöjen"; `lahti` ei osu sanaan "lahden", koska sanan alku muuttuu). `sana*` = mikä tahansa jatko (`Vantaanjo*` → "Vantaanjokeen"), `*sana` = myös yhdyssanan loppuosa (`*joki` → "Mätäjoki"). Lyhyet sanat osuvat herkästi: `järven` osui sanaan "Järvenpää".
- Halutaan seurata myös päätösten valitusaikoja → `non_response_actions_max_priority.päätös: "medium"`.
- Yksittäinen kohde → lisää `state/items.json`-riville `"manual_priority": "high" | "medium" | "low" | "exclude"`. Säilyy ajosta toiseen.

Kaikki muutokset `config/`-kansioon tulevat voimaan seuraavassa ajossa; skripti pisteyttää koko tilan uudelleen joka kerta.

## Rakenne

```
config/sources.json    seurattavat lähteet (url, lukutapa, hakukyselyt, verify-lippu)
config/kunnat.json     16 kuntaa, aliakset
config/keywords.json   pisteytyssäännöt
scripts/build_calendar.py
state/items.json       seurannassa olevat kohteet (tila)
state/archive.json     vanhentuneet (määräpäivästä > 60 pv)
incoming/<pvm>.json    kunkin ajon raakapoiminta
docs/summary-<pvm>.md  viikkoyhteenvedot
docs/all-items.md      kaikki seurannassa olevat, taulukkona
calendar.ics           tilattava kalenteri
TASK_PROMPT.md         ajastetun tehtävän ohje
```

## Tunnetut rajoitteet

- `lvv.fi` ja `lausuntopalvelu.fi` estävät automaattisen lukemisen. Ne käydään läpi hakukoneen indeksin kautta, joka laahaa muutaman päivän. LVV:n vesi- ja ympäristölupakuulutukset ovat heikoimmin katettu osa; tarkista Ympäristöasioiden tietopalvelu käsin, jos jokin iso hanke on tiedossa.
- Kuntien kuulutussivujen osoitteet ovat osin arvauksia (`verify: true`); ensimmäiset ajot korjaavat ne.
- Määräpäivät luetaan tekstistä. Jos kuulutuksessa lukee vain "nähtävillä 30 päivää", päivä on arvio.
- Poiminnan tekee kielimalli, ei jäsennin: se voi jättää kohteen huomaamatta tai lukea päivämäärän väärin. Yhteenvedossa on linkit, tarkista tärkeät.

## Ajaminen käsin

```
python3 scripts/build_calendar.py --incoming incoming/2026-11-02.json --today 2026-11-02 --failed lvv_tietopalvelu
```
