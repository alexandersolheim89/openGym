# Steg 2: Garmin-synk

`garmin-sync` er en liten tjeneste på serveren. Hver 30. minutt henter den nye aktiviteter fra
Garmin Connect og legger dem inn i openGym som økter, med tid, distanse, fart og puls.

| Garmin | I openGym |
|---|---|
| Løping, terrengløp | Run |
| Mølle | Run on treadmill |
| Gåtur, fottur | Walking |
| Sykkel, terrengsykkel | Riding outdoor bicycle |
| Innesykkel | Stationary bike |
| El-sykkel | Riding outdoor bicycle, merket «el-sykkel» i notatet |
| Romaskin, crosstrainer | Rowing, elliptical |
| Inneklatring, buldring, klatring | «Klatring» (egen øvelse, lages første gang) |
| **Styrke** | **Hoppes over.** Den logger du i openGym, ellers blir den dobbel. |

Hver økt får et notat som `Fra Garmin · 5,21 km · 28:14 · 5:25 /km · snittpuls 148 · 312 kcal`.
Samme aktivitet legges aldri inn to ganger, og en økt du sletter i appen kommer ikke tilbake.
Første gang hentes de siste 30 dagene.

**Viktig å vite:** tjenesten bruker det uoffisielle biblioteket `python-garminconnect`. Endrer
Garmin innloggingen sin, kan synken stoppe til biblioteket er oppdatert. Ingen data går tapt.

---

Alt under skjer **på serveren** (`ssh -i … ubuntu@79.76.57.22`).

## 1. Hent den nye versjonen

```bash
cd ~/openGym && git pull && cd treningsapp
```

## 2. Gi tjenesten tilgang til din openGym-profil

1. I **Safari** på iPhonen: `https://solheim-trening.duckdns.org` → **Settings** → **Pair the mobile app** → Face ID.
2. På serveren, bytt `KODEN` med koden (innen 5 minutter):

```bash
T=$(curl -s -X POST https://solheim-trening.duckdns.org/api/pair/redeem -H 'Content-Type: application/json' -d '{"code":"KODEN"}' | grep -o '"token":"[^"]*"' | cut -d'"' -f4); echo "Lengde: ${#T}"
```

`Lengde:` skal være over 50. Legg nøkkelen i `.env`:

```bash
sed -i '/^GARMIN_SYNC_OPENGYM_TOKEN=/d' .env && echo "GARMIN_SYNC_OPENGYM_TOKEN=$T" >> .env && unset T && grep -c GARMIN_SYNC_OPENGYM_TOKEN .env
```

Den skal skrive `1`.

## 3. Bygg tjenesten

```bash
docker compose build garmin-sync
```

Tar et par minutter første gang på den lille serveren.

## 4. Logg inn på Garmin (én gang)

```bash
mkdir -p garmin-tokens && docker compose run --rm garmin-sync login
```

Skriv e-post og passord til Garmin. Passordet vises ikke mens du skriver, og det lagres ikke.
Har du totrinnsbekreftelse, spør den etter koden Garmin sender deg.

## 5. Prøv uten å lagre

```bash
docker compose run --rm garmin-sync once --dry-run
```

Den viser hvilke økter den **ville** lagt inn, uten å endre noe i openGym.

## 6. Start den

```bash
docker compose up -d garmin-sync && docker compose logs -f garmin-sync
```

Etter noen sekunder skal du se `Lagt inn …`-linjer for øktene. **Ctrl+C** for å slutte å følge med.
Tjenesten går videre i bakgrunnen og starter av seg selv når serveren starter.

## Hente eldre aktiviteter på nytt

Etter at nye aktivitetstyper er lagt til, henter vanlig synk bare de siste 3 dagene. For å ta
med eldre (allerede innlagte økter dobles ikke):

```bash
docker compose run --rm garmin-sync once --days 30
```

## Når noe stopper

```bash
docker compose logs --tail 30 garmin-sync
```

- **`FEIL: … 401`** fra openGym: nøkkelen i `.env` er utløpt eller trukket tilbake. Gjør steg 2
  på nytt, og kjør `docker compose up -d garmin-sync`.
- **Feil om innlogging til Garmin**: kjør steg 4 på nytt. Hjelper ikke det, har Garmin endret
  noe. Oppdater `garminconnect` i `garmin-sync/requirements.txt` og bygg på nytt (steg 3).

## Fruen

Hun trenger sin egen tjeneste med sin egen Garmin-innlogging og sin egen openGym-nøkkel. Det
legger vi til når hun er klar.
