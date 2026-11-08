# Treningsapp

Treningsappen vår: [openGym](https://github.com/DuarteSantos8/openGym) på vår egen gratis server,
koblet mot Garmin Venu 4 og iPhone.

Alt vårt ligger i denne mappen. Resten av repoet er openGym slik det er hos utvikleren, så
forken kan holdes oppdatert med *Sync fork* på GitHub uten konflikter.

## Mål

1. **All treningsdata på ett sted.** Styrkeøkter, løp, gåturer og sykkel fra Garmin havner i openGym,
   for oss begge.
2. **Bygg økta i appen og logg den på klokka.** En økt laget i openGym dukker opp som styrkeøkt på
   Venu 4. Det du logger på klokka (sett, repetisjoner, vekt, puls) kommer tilbake i openGym.

## Hvordan det henger sammen

```
 iPhone (openGym-appen) ─┐
                         ▼
 ┌──────────── Oracle Cloud (gratis), docker compose ────────────┐
 │ caddy (HTTPS) → web (openGym) → api (openGym) → data/          │
 │                                     ▲                          │
 │                                     │ /api/data                │
 │                 garmin-sync  ◄──────┘          (steg 2)        │
 └──────────────────────┬─────────────────────────────────────────┘
                        │ python-garminconnect (uoffisielt)
                        ▼
                 Garmin Connect ◄──► Venu 4
```

openGym kjører uendret fra de ferdige imagene, så vi får oppdateringene til utvikleren gratis.
Alt vi lager selv ligger i `garmin-sync`.

## Status

- [ ] **Steg 1: server og openGym.** Se [docs/1-server-oracle.md](docs/1-server-oracle.md).
- [ ] **Steg 2: Garmin → openGym.** `garmin-sync` henter aktiviteter (med sett og puls) for oss
      begge og skriver dem inn i openGym.
- [ ] **Steg 3: openGym → Garmin.** Rutiner fra openGym lastes opp som styrkeøkter i Garmin Connect
      og legges i kalenderen, så de ligger klare på klokka. Krever en tabell som kobler
      openGym-øvelser til Garmin-øvelser.
- [ ] **Senere:** openGym sin egen klokkeapp for Venu 4 (`frontend/garmin` hos openGym). Den
      venter på at koblingen mot klokka blir ferdig i iPhone-appen.

## Ting å vite

- **Passkeys er låst til domenet.** Bytt aldri `RP_ID` etter at profilene er laget.
- **Dobbelttelling:** Garmin skriver økta til Apple Helse. Ikke slå på openGyms eksport av økter
  til Apple Helse, ellers telles kaloriene to ganger.
- **Garmin-koblingen er uoffisiell** (python-garminconnect). Den kan slutte å virke når Garmin
  endrer innloggingen. Da stopper synken til biblioteket er oppdatert. Ingen data går tapt.
- **Backup:** `data/` krypteres og lastes opp til Google Drive hver natt (`scripts/backup.sh`).
