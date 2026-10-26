# Lausuntovahti – viikkoajon ohje

Tämä on viikoittaisen ajastetun tehtävän ohje. Jokainen ajo alkaa tyhjästä istunnosta, joten kaikki tarvittava on tässä tiedostossa ja repossa.

## Tavoite

Etsi Vantaanjoen, Espoonjoen ja Mankinjoen valuma-alueiden kuntia ja viranomaisia koskevat **uudet kuulutukset, joihin voi vielä vastata** (mielipide, muistutus, lausunto, vastine), kirjaa ne JSON-muotoon, aja pisteytys- ja kalenteriskripti, committaa ja lähetä lyhyt yhteenveto. Kalenteriin menevät vain high- ja medium-prioriteetin kohteet; loput näkyvät vain yhteenvedossa.

## Vaiheet

1. **Repo.** Kloonaa tai päivitä repo (`git pull`). Lue `config/sources.json`, `config/kunnat.json`, `config/keywords.json` ja `state/items.json` (jo tiedossa olevat kohteet). Älä kirjaa tunnettua kohdetta uudelleen, ellei sen määräpäivä tai linkki tarkennu. Jos kirjaat, lisää riville kenttä `"id"`, jonka arvo on kohteen `id` tiedostosta `state/items.json` – muuten skripti tekee siitä uuden kohteen ja kalenteriin tulee kaksoiskappale. Sama koskee kuulutusta, joka on jo tiedossa toisesta lähteestä tai eri otsikolla.

2. **Lähteet.** Käy läpi jokainen `config/sources.json`-lähde joka ajossa, myös jos samana päivänä on jo ajettu – aiempi ajo ei korvaa tätä. Ajo, joka ohittaa lähteitä, on kesken.
   - `method: fetch` → lue sivu WebFetchillä. Pyydä listaus kaikista kuulutuksista/ilmoituksista, joissa on päivämäärä ja jotka koskevat seurattavia kuntia.
   - Listaussivut näyttävät usein vain uusimmat kuulutukset (esim. Espoo 12, Helsinki 10 sivulla). Selaa seuraaville sivuille, kunnes vastaan tulee kuulutuksia, jotka on julkaistu yli 14 päivää sitten. Jos listaus ei sivuta, käytä lähteen `notes`-kentässä kerrottua tapaa tai sivuston hakua. Vanhakin kuulutus kirjataan, jos siihen voi vielä vastata.
   - `method: search` tai `fetch_then_search` (kun fetch epäonnistuu) → aja `search_queries` korvaten `{kunta}` seurattavilla kunnilla. Riittää, että kyselyt ajetaan 4–6 kunnalle, jotka osuvat todennäköisimmin (Vantaa, Espoo, Nurmijärvi, Tuusula, Hyvinkää, Riihimäki), plus kyselyt ilman kuntaa. Käytä `mode: standard`.
   - `verify: true` → jos osoite ei toimi, etsi oikea (WebSearch: "<kunta> kuulutukset"), päivitä `config/sources.json` (url, `verify: false`) ja jatka. Kirjaa muutos yhteenvetoon.
   - Jos WebFetch ei saa sivua auki (esim. `ENOTFOUND` tai 403), kokeile sille lähteelle WebSearchia (`site:<domain> kuulutus`) ennen kuin luovutat. Jos lähde ei aukea millään tavalla, lisää sen `id` epäonnistuneisiin ja jatka seuraavaan. Käy kaikki lähteet läpi, vaikka useampi epäonnistuisi. Älä yritä kiertää estoja curlilla tai muilla keinoilla.
   - Ohita selvästi epäolennaiset: rallit, triathlonit, laajakaistahankkeet, joukkoliikennepäätökset, ajoneuvojen siirrot, virkailmoitukset. Skripti suodattaa nämä joka tapauksessa, mutta niitä ei tarvitse kirjata.

3. **Poiminta.** Kirjoita löydökset tiedostoon `incoming/<YYYY-MM-DD>.json` muodossa `{"items": [ ... ]}`. Jos tiedosto on jo olemassa (saman päivän aiempi ajo), lisää kohteet sen `items`-listaan. Kenttäkuvaus on `scripts/build_calendar.py`-tiedoston alussa. Säännöt:
   - `title`: julkaistu otsikko lyhennettynä (max ~120 merkkiä), ei päivämääriä otsikkoon.
   - `url`: suorin linkki kuulutukseen tai asiakirjoihin (PDF käy). Ei etusivuja.
   - `kunta`: lista `config/kunnat.json`-nimiä. Tyhjä lista, jos alueellinen (esim. vesienhoitosuunnitelma).
   - `type`: käytä näitä kun sopii: `vesilupa`, `ympäristölupa`, `maa-aineslupa`, `YVA`, `asemakaava`, `yleiskaava`, `maakuntakaava`, `tiesuunnitelma`, `ratasuunnitelma`, `katusuunnitelma`, `vesienhoito`, `merenhoito`, `tulvariski`, `luonnonsuojelu`, `rakennusjärjestys`, `valitus`, `muu`.
   - `action`: mihin voi vastata: `mielipide` (OAS/luonnos), `muistutus` (ehdotus/lupahakemus), `lausunto` (lausuntopyyntö), `vastine` (hallinto-oikeus), `tiedoksi` (ei vastausmahdollisuutta), `päätös`.
   - `deadline`: viimeinen päivä, jona voi vastata, muodossa `YYYY-MM-DD`. Jos kuulutuksessa lukee vain nähtävilläoloaika, määräpäivä = nähtävilläolon viimeinen päivä, `deadline_confidence: exact`. Jos vain julkaisupäivä tiedossa ja kyse on LVV:n lupakuulutuksesta tai kaavaehdotuksesta, saa arvioida julkaisupäivä + 30 pv, `deadline_confidence: estimated`. Muuten `null` ja `unknown`. Älä keksi päivämääriä.
   - `summary`: 1–2 lausetta: mikä hanke, missä, miksi se voi kiinnostaa vesiensuojeluyhdistystä (vesistö, hulevedet, jätevedet, pohjavesi, ranta). Kaavat liittyvät aina veteen hulevesien hallinnan kautta. Jos kohde ei liity veteen, kirjoita täsmälleen "Ei liity veteen." – älä luettele vesiasioita, joita kohde ei koske, koska vesisanat nostavat pisteitä kieltolauseessakin.
   - `id`: vain kun päivität jo tiedossa olevaa kohdetta (ks. vaihe 1).
   - Jos kohde on vastattavissa (mielipide/muistutus/lausunto/vastine) eikä listaussivulla ole määräpäivää, avaa itse kuulutus (usein PDF) ja etsi nähtävilläolon tai vastausajan päättymispäivä ennen kuin jätät sen `null`:ksi.
   - Ota mukaan myös high/medium-kohteet ilman määräpäivää; ne eivät ole kalenterissa, mutta yhteenveto listaa ne joka viikko käsin tarkistettaviksi.

4. **Skripti.** Aja `python3 scripts/build_calendar.py --incoming incoming/<pvm>.json --today <pvm> --failed <epäonnistuneet lähde-id:t>`. Skripti yhdistää, pisteyttää, kirjoittaa `calendar.ics`, `state/items.json`, `docs/summary-<pvm>.md` ja tulostaa yhteenvedon. Jos skripti kaatuu, korjaa JSON (yleensä virheellinen päivämäärä tai puuttuva kenttä) ja aja uudestaan. Älä muuta skriptin logiikkaa ajon aikana.

5. **Commit ja push.** `git add -A && git commit -m "Viikkoajo <pvm>: +N uutta, M päivitettyä" && git push`. Jos push epäonnistuu, raportoi se yhteenvedossa. Committaa ja pushaa myös silloin, kun uusia kohteita ei löytynyt: tila ja arkistointi päivittyvät joka ajossa. Saman päivän uusinta-ajo korvaa `docs/summary-<pvm>.md`:n, ja se on tarkoituksellista.

6. **Yhteenveto.** Lähetä `PushNotification`-työkalulla (jos käytettävissä) `docs/summary-<pvm>.md`-sisältö tiivistettynä: uudet high- ja medium-kohteet taulukkona (otsikko, kunta, määräpäivä, linkki), määräpäivät seuraavan 14 päivän sisällä, high/medium-kohteet ilman määräpäivää, ja lähteet, joiden luku epäonnistui. Low-kohteista vain lukumäärä. Jos mitään uutta ei löytynyt, sano se yhdellä rivillä. Linkki kalenteriin: `https://raw.githubusercontent.com/antti-a/lausuntovahti/main/calendar.ics`. Lopeta ajo viestiin, jossa on sama yhteenveto ja lisäksi taulukko kaikista lähteistä: `id`, mitä tehtiin (luettu / haettu / epäonnistui ja miksi), löydettyjen kohteiden määrä ja mahdollinen korjattu osoite.

## Rajat

- Älä lähetä lausuntoja, muistutuksia tai sähköposteja. Tämä tehtävä vain seuraa.
- Älä muokkaa `config/keywords.json`-sääntöjä; ehdota muutoksia yhteenvedossa, jos huomaat toistuvia vääriä osumia.
- Jos jokin lähde näyttää vaihtaneen osoitetta tai muotoa, kirjaa havainto `config/sources.json`-kentän `notes` alle.
- Pidä ajo kohtuullisena: n. 25 lähdettä, muutama haku per lähde. Ei syväluotaavaa tutkimusta.
