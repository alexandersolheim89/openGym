# Steg 1: Gratis server hos Oracle Cloud

Dette tar 1–2 timer første gang. Når du er ferdig, har du:

- en gratis Linux-server i Stockholm
- openGym på `https://<navn>.duckdns.org` med HTTPS
- en profil til deg og en til fruen, og iPhone-appen koblet til
- en kryptert backup til Google Drive hver natt, og varsel på e-post hvis siden går ned

## Er det virkelig gratis, og vil det fungere?

**Kostnad: ja, 0 kr**, så lenge du **ikke** trykker «Upgrade» på kontoen. Kontoen er da en
Free Tier-konto. Etter de første 30 prøvedagene kan den bare bruke «Always Free»-ressurser og
kan ikke belastes. Kortet brukes bare til å bekrefte hvem du er.

**At den alltid står der: nesten, men ikke garantert.** Dette er de kjente risikoene og hva vi
gjør med dem:

| Risiko | Hva vi gjør |
|---|---|
| Oracle kan stoppe en gratis maskin som har vært nesten ubrukt i 7 dager (CPU, nettverk og minne under 20 %). | Vi følger med på siden fra utsiden (steg 10), så du får e-post med én gang. Hele oppsettet kan settes opp på nytt på 15 minutter fra backupen. |
| «Out of capacity» når du lager maskinen. Det er ofte fullt for ARM-maskiner. | Prøv igjen senere på dagen eller kvelden. Det løser seg nesten alltid etter noen forsøk. |
| Oracle endrer gratisvilkårene. Det skjedde i 2026: grensen ble halvert til 2 CPU og 12 GB. | Vi bruker bare 1 CPU og 6 GB, godt under grensen. |

Det som ikke kan gå tapt, er dataene: de ligger i den nattlige backupen (steg 9).

---

## 1. Lag Oracle-konto

1. Gå til **oracle.com/cloud/free** og velg *Start for free*.
2. **Home Region: velg `Sweden Central (Stockholm)`.** Dette kan *ikke* endres senere, og
   gratismaskinene finnes bare i hjemmeregionen.
3. Fyll inn kortet. Oracle reserverer et lite beløp som tilbakeføres.
4. Når kontoen er klar: **ikke trykk «Upgrade to Pay As You Go».**

## 2. Lag serveren

Menyen ☰ → **Compute → Instances → Create instance**.

| Felt | Velg |
|---|---|
| Name | `treningsapp` |
| Image | **Canonical Ubuntu 24.04** |
| Shape | *Change shape* → **Ampere** → **VM.Standard.A1.Flex**, **1 OCPU**, **6 GB** minne. Det skal stå «Always Free-eligible». |
| Networking | *Create new virtual cloud network* og *public subnet*. **Assign a public IPv4 address: Yes.** |
| SSH keys | **Generate a key pair for me** → *Save private key*. Ta godt vare på filen (`ssh-key-….key`). |
| Boot volume | La standard stå (ca. 47 GB, gratis opp til 200 GB). |

Trykk **Create**. Får du *Out of capacity*, prøv igjen litt senere.

Er det fullt over lang tid, bruk den lille gratismaskinen i stedet: *Change shape* →
**Specialty and previous generation** → **VM.Standard.E2.1.Micro** (1/8 CPU, 1 GB minne). Velg
**Canonical Ubuntu 24.04** på nytt etterpå. Den holder fint for to personer, men trenger swap
(steg 5).

Når den står som **Running**: noter **Public IP address**.

## 3. Åpne portene for nettsiden (i Oracle)

På serverens side: klikk på **Subnet**-lenken → **Security Lists** → *Default Security List* →
**Add Ingress Rules**. Legg til to regler:

| Source CIDR | IP Protocol | Destination Port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

## 4. Logg inn på serveren

På Mac (Terminal) eller Windows (PowerShell):

```bash
chmod 600 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@DIN_IP
```

Alt videre i denne guiden skrives **på serveren**.

## 5. Åpne brannmuren på serveren og installer Docker

Ubuntu hos Oracle har en egen brannmur som stenger alt. Åpne 80 og 443:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

**Bare på den lille maskinen (VM.Standard.E2.1.Micro, 1 GB minne):** legg inn 2 GB
reserveminne (swap), så serveren ikke går tom for minne:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

`free -h` skal nå vise `Swap: 2.0Gi`.

Installer Docker:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit
```

Logg inn igjen med `ssh` som i steg 4, så tar endringen effekt.

## 6. Gratis domene med DuckDNS

1. Gå til **duckdns.org** og logg inn, for eksempel med Google.
2. Lag et navn, for eksempel `mitt-gym`. Lim inn serverens IP og trykk *update ip*.
3. Kopier **token** øverst på siden.

Sørg for at navnet alltid peker på riktig IP (sjekkes hvert 5. minutt):

```bash
mkdir -p ~/duckdns
printf '#!/bin/sh\ncurl -fsS "https://www.duckdns.org/update?domains=mitt-gym&token=DIN_TOKEN&ip=" > ~/duckdns/siste.log\n' > ~/duckdns/oppdater.sh
chmod 700 ~/duckdns/oppdater.sh
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/oppdater.sh") | crontab -
```

## 7. Hent prosjektet og start openGym

Prosjektet ligger i forken din av openGym, i mappen `treningsapp/`. Forken er offentlig, så
serveren trenger ingen nøkkel for å hente den.

```bash
git clone --depth 1 https://github.com/alexandersolheim89/openGym.git
cd openGym/treningsapp
cp .env.example .env
nano .env        # bytt mitt-gym.duckdns.org med ditt navn på alle tre linjene. Ctrl+O, Enter, Ctrl+X
docker compose up -d
docker compose logs -f caddy   # vent til du ser "certificate obtained successfully", så Ctrl+C
```

Sjekk at alt svarer:

```bash
curl https://mitt-gym.duckdns.org/api/health
```

## 8. Lag profilene og koble til iPhone

1. På **din** iPhone: åpne `https://<navn>.duckdns.org` i Safari → **Create profile** → Face ID.
   Du blir admin fordi du er først.
2. Fruen gjør det samme på sin iPhone.
3. Steng for andre. På serveren: `nano .env`, fjern `#` foran `INVITE_ONLY=1`, og kjør
   `docker compose up -d`.
4. **iPhone-appen:** last ned *openGym* fra App Store. I Safari (innlogget): *Settings → Account →
   Pair the mobile app*. Skriv inn koden i appen.
5. **Garmin-puls per sett:** i appen *Settings → Integrations → Apple Health* → slå på *Heart
   rate from your watch*. I Garmin Connect-appen: finn *Apple Helse* under innstillingene
   (tilkoblede apper), og la Garmin dele data dit. Det er pulsen openGym trenger.

## 9. Nattlig backup til Google Drive (kryptert)

Backupen krypteres på serveren før den lastes opp, så Google kan ikke lese den.

```bash
curl https://rclone.org/install.sh | sudo bash
sudo rclone config
```

Svar slik:

1. `n` (ny remote) → navn **`gdrive`** → velg **Google Drive** → `client_id` og
   `client_secret` blanke → scope **`drive.file`**.
2. *Use web browser to automatically authenticate?* → **`n`**. Du får en kommando som begynner
   med `rclone authorize "drive" …`.
3. **På Mac/PC-en din:** installer rclone (rclone.org/downloads), kjør den kommandoen, logg inn
   med Google, og lim svaret tilbake i serveren.
4. `n` (ny remote igjen) → navn **`gdrive-krypt`** → velg **Encrypt/Decrypt a remote** →
   remote: **`gdrive:treningsapp-backup`** → la den lage passord (`g`).
   **Skriv passordene inn i passordbehandleren din.** Uten dem kan backupen ikke åpnes.
5. `q` for å avslutte.

Test og sett opp kjøring hver natt kl. 03:30:

```bash
sudo bash ~/openGym/treningsapp/scripts/backup.sh
(sudo crontab -l 2>/dev/null; echo "30 3 * * * bash /home/ubuntu/openGym/treningsapp/scripts/backup.sh >> /var/log/treningsapp-backup.log 2>&1") | sudo crontab -
```

**Gjenopprette** (ny server, samme steg 1–7, før `docker compose up -d`):

```bash
sudo rclone copy gdrive-krypt:opengym-ÅÅÅÅ-MM-DD.tar.gz .
sudo tar xzf opengym-ÅÅÅÅ-MM-DD.tar.gz
docker compose up -d
```

Husk å ta vare på rclone-oppsettet også: `sudo cat /root/.config/rclone/rclone.conf`, lagret i
passordbehandleren.

## 10. Varsel hvis noe stopper (gratis)

- **UptimeRobot** (uptimerobot.com): ny HTTPS-monitor mot
  `https://<navn>.duckdns.org/api/health`. Du får e-post hvis siden er nede, for eksempel hvis
  Oracle har stoppet maskinen.
- **healthchecks.io**: lag en sjekk med periode 1 dag. Legg URL-en inn i root sin crontab foran
  backup-linjen: `HEALTHCHECK_URL=https://hc-ping.com/… bash /home/ubuntu/…`. Da får du e-post
  hvis backupen ikke har kjørt.

## Vedlikehold

Én gang i måneden, eller når openGym har sluppet en ny versjon:

```bash
cd ~/openGym && git pull && cd treningsapp && docker compose pull && docker compose up -d
sudo apt update && sudo apt upgrade -y
```
