# Signatures

Les moteurs de détection d'une borne n'utilisent qu'un **jeu de signatures
signé**, vérifié à chaque démarrage. Un jeu s'installe depuis une **clé de
mise à jour des signatures** (une clé USB contenant un jeu, pour les bornes
sans réseau) ou, quand c'est activé, **en ligne**. Sans jeu valide, la borne
n'analyse rien : elle affiche `NO VALID SIGNATURES` et attend une clé de
mise à jour des signatures.

## En bref { #in-short }

Sur l'ordinateur qui détient la clé de mise à jour, par exemple chaque jour :

```sh
export ABUSECH_AUTH_KEY=...
publish/publish.sh /srv/pandora-box/pandora-box-signatures /secure/update.key
cp -r /srv/pandora-box/pandora-box-signatures /media/$USER/KEY/
```

Insérez la clé dans la borne : elle installe le jeu, recharge ses moteurs et
demande de retirer la clé. Une borne en ligne le fait d'elle-même toutes les
6 heures.

## Mettre à jour une borne { #updating-a-kiosk }

Copiez un jeu signé dans un dossier `pandora-box-signatures` à la racine
d'une clé USB (vfat, exFAT, NTFS ou ext4) et insérez-la dans la borne. La
borne vérifie et installe le jeu, recharge ses moteurs, puis demande de
retirer la clé : une clé de mise à jour des signatures n'est jamais
analysée, ne l'utilisez donc pas pour transporter d'autres fichiers.

La borne affiche et journalise le résultat (`signatures_installed`,
`signatures_refused` avec la raison, ou `signatures_not_newer`). Un jeu
n'est installé que s'il est plus récent que celui en place.

La borne avertit quand ses signatures vieillissent (voir
[Fraîcheur des signatures](engines.md#signature-freshness)).

## Préparer un jeu { #preparing-a-set }

`pandora-box-signatures publish` télécharge les signatures de chaque moteur
depuis leurs sources, vérifie que les moteurs d'une borne peuvent les
charger, puis construit et signe un jeu (`src/pandora_box/publish.py`) :

| Source | Fichiers | Remarques |
|---|---|---|
| `clamav` | `clamav/main.cvd`, `daily`, `bytecode` | mis à jour par `freshclam` (incrémental), qui vérifie leur signature Cisco Talos |
| `yara-forge` | `yara/yara-forge/yara-rules-core.yar` | paquet « core » de YARA Forge, dernière version publiée sur GitHub |
| `signature-base` | `yara/signature-base/*.yar` | sur demande : YARA Forge inclut déjà ses règles |
| `malwarebazaar` | `malwarebazaar/malwarebazaar.sha256.bin` | export SHA-256 complet, nécessite une Auth-Key abuse.ch gratuite (`ABUSECH_AUTH_KEY`) |
| `hashlookup` | `hashlookup/hashlookup-full.bloom` | environ 1 Go, publié chaque mois |

La publication s'exécute dans un conteneur contenant freshclam, clamscan et
les moteurs, par exemple chaque jour sur l'ordinateur qui détient la clé de
signature :

```sh
export ABUSECH_AUTH_KEY=...
publish/publish.sh /srv/pandora-box/pandora-box-signatures /secure/update.key
cp -r /srv/pandora-box/pandora-box-signatures /media/KEY/
```

Les téléchargements se font uniquement en HTTPS et sont mis en cache
(`--cache`, par défaut `~/.cache/pandora-box-signatures`) : une source qui
n'a pas changé (ETag, Last-Modified) n'est pas téléchargée à nouveau. Avant
la signature, le jeu est chargé avec les moteurs de la borne : les règles
YARA doivent compiler avec YARA-X, la base MalwareBazaar et le filtre
Hashlookup doivent se charger, et `clamscan` doit charger les bases ClamAV.
Un jeu qui échoue n'est jamais signé, et le dossier de sortie précédent est
conservé : un jeu défectueux, mais valablement signé, empêcherait d'analyser
toutes les bornes qui l'installent. Le jeu doit contenir les fichiers de
chaque moteur activé sur les bornes : une borne refuse d'analyser avec un
jeu auquel il en manque un.

Le jeu complet fait environ 1,2 Go (ClamAV environ 115 Mo, YARA Forge core
8 Mo, Hashlookup 1 Go). Avec les bases ClamAV complètes, clamd utilise
environ 1 Go de mémoire sur la borne (mesuré dans la machine virtuelle de
test). La signature utilise `openssl`, pour que la clé privée puisse rester
dans un jeton matériel (fournisseur OpenSSL). La clé de publication du
projet et son stockage ne sont pas encore définis.

La commande fonctionne aussi sur des jeux construits par d'autres moyens :

```sh
pandora-box-signatures build FOLDER --key update.key   # manifest.json + signature
pandora-box-signatures verify FOLDER --keys KEYS_FOLDER
pandora-box-signatures install FOLDER                   # à la main, sur une borne
```

`build` et `publish` numérotent le jeu avec sa date de création
(`YYYYMMDDHHMMSS`), sauf si `--serial` est donné.

## Mises à jour en ligne { #online-updates }

Une borne fonctionne hors ligne par défaut. Une image construite avec le
**profil `online`** ([Construction de l'image](build.md#profile-online)) met
à jour ses signatures elle-même, **depuis leurs sources** : ClamAV
(freshclam), YARA Forge, MalwareBazaar et Hashlookup, les sources de
`pandora-box-signatures publish`. Le profil apporte DHCP sur le réseau
filaire (IPv4), le DNS (systemd-resolved, sans LLMNR ni mDNS), NTP
(systemd-timesyncd, [Heure](build.md#time)), freshclam, le timer
`pandora-box-update` (toutes les 6 heures, 5 minutes après le démarrage), et
un pare-feu qui ne laisse sortir que le service de mise à jour (TCP),
l'export des journaux vers un SIEM ([SIEM](siem.md)), DHCP, DNS et NTP. Rien
n'entre, à part les réponses à ces connexions. Il active la section
`[updates]` de la configuration avec toutes les sources :

```toml
[updates]
enabled = true
sources = ["clamav", "yara-forge", "malwarebazaar", "hashlookup"]
# Facultatif : un miroir de l'entreprise à la place d'une source ("clamav" :
# un miroir privé freshclam), et un proxy
# mirrors = { hashlookup = "https://mirror.example.org/hashlookup-full.bloom" }
# proxy = "http://proxy.example.org:3128"
```

La borne charge le nouveau jeu la prochaine fois qu'elle est au repos (entre
deux clés) : une analyse n'est jamais interrompue. Elle journalise
`signatures_changed` et affiche `New signatures installed`. Les clés de mise
à jour des signatures restent acceptées (une borne coupée du réseau, ou des
signatures plus récentes que le dernier téléchargement).

### Identifiants { #credentials }

MalwareBazaar nécessite une **Auth-Key abuse.ch** (compte gratuit :
https://auth.abuse.ch/). Elle est fournie à la construction dans
`image/credentials.toml` (voir `image/credentials.toml.example` ; git ignore
ce fichier), et le profil l'installe sur le système de fichiers racine en
lecture seule comme **identifiant systemd**,
`/etc/credstore/pandora-box.abusech-auth-key`, lisible par root uniquement :
systemd la transmet au seul service de mise à jour (`ImportCredential=`).
Sans elle, MalwareBazaar n'est pas téléchargé et sa base reste celle de la
dernière clé de mise à jour des signatures.

> [!WARNING]
> Une image construite avec des identifiants **ne doit pas être publiée** :
> quiconque possède l'image ou le disque de la borne peut les lire.

### Un jeu publié à la place { #a-published-set-instead }

Une borne peut aussi télécharger un jeu signé publié par
`pandora-box-signatures publish` sur un serveur web, à la place des
sources : `url` (le dossier du jeu) au lieu de `sources`. Elle n'a alors
besoin d'aucun identifiant, et n'installe qu'un jeu signé avec la clé de
mise à jour, plus récent que celui en place, en ne téléchargeant que les
fichiers modifiés.

Le même service installe aussi les mises à jour en ligne de l'image
(`updates.image_url`, voir [Mises à jour du système](update.md#online)).

### Le service de mise à jour { #the-update-service }

`pandora-box-update.service` fonctionne en deux étapes
(`src/pandora_box/online.py`) :

1. sous l'utilisateur `pandora-box-update`, que le pare-feu laisse sortir,
   sans privilèges et dans un bac à sable systemd, il télécharge les sources
   (avec un cache HTTP, `/var/lib/pandora-box-update/staging-sources` : une
   source inchangée n'est pas téléchargée à nouveau ; freshclam télécharge
   les différences quotidiennes de ClamAV), vérifie que les moteurs les
   chargent, et construit un jeu dans `/var/lib/pandora-box-update/staging`
   quand elles ont changé. Les fichiers des sources qu'il ne télécharge pas
   sont repris du jeu installé ;
2. en root, sans réseau, il signe le jeu préparé avec la **clé de la borne**
   et l'installe (`install --staged`) : toutes les vérifications d'une clé
   de mise à jour des signatures s'appliquent, les fichiers inchangés sont
   repris du jeu installé.

La clé de la borne est une clé Ed25519 générée sur la borne la première fois
(`/var/lib/pandora-box-signatures/local-key/`, la clé privée lisible par
root uniquement). La borne lui fait confiance en plus des clés de son image :
elle vérifie le jeu installé à chaque démarrage, quel qu'en soit le
signataire.

Les téléchargements se font en HTTPS (un miroir configuré dans
`updates.mirrors` peut être en HTTP) ; les bases ClamAV portent la signature
de Cisco Talos, vérifiée par freshclam. freshclam ne charge pas les
nouvelles bases pour les tester (`TestDatabases no`) : clamd les a déjà en
mémoire, une borne a 2 Go. Les redirections ne sont suivies que vers des URL
HTTPS, et le proxy de l'environnement est ignoré : seul `updates.proxy` est
utilisé.

## Jeux de signatures { #signature-sets }

Un jeu de signatures est un dossier contenant les fichiers de signatures des
moteurs et leur liste signée (`src/pandora_box/sigsets.py`) :

```
manifest.json          numéro de série, date de création, SHA-256 et taille de chaque fichier
manifest.json.sig      signature Ed25519 de manifest.json
clamav/main.cvd        bases ClamAV (clamd lit ce dossier)
clamav/daily.cvd
malwarebazaar/malwarebazaar.sha256.bin
hashlookup/hashlookup-full.bloom
yara/yara-forge/yara-rules-core.yar
```

Les chemins sont ceux de la configuration par défaut
(`/var/lib/pandora-box-signatures/current/...`). Le `manifest.json` d'un
sous-dossier (écrit par `pandora-box-signatures publish`) donne la source,
la version et la date de ses fichiers ; sans lui, la date d'un fichier est
la date de création du jeu, utilisée pour les avertissements de fraîcheur
des signatures.

## Confiance { #trust }

- La borne fait confiance aux clés publiques Ed25519 (`*.pem`) de
  `signatures.keys`, `/usr/share/pandora-box/keys/` dans l'image : elles font
  partie du système de fichiers racine, protégé par dm-verity et Secure
  Boot. La clé de mise à jour est distincte de la clé de signature de
  l'image : les jeux sont signés bien plus souvent.
- La signature est vérifiée avec `openssl`, sur les octets exacts du
  manifeste, avant que quoi que ce soit d'autre du jeu ne soit lu.
- Un jeu n'est installé que si son numéro de série est supérieur à celui du
  jeu installé : un jeu plus ancien, même valablement signé, est refusé (pas
  de retour en arrière).
- Chaque fichier est copié en vérifiant sa taille et son SHA-256 d'après le
  manifeste ; les fichiers non listés sont ignorés ; les chemins sont des
  noms simples (pas de `..`, pas de nom caché) et les liens ne sont jamais
  suivis.
- Le jeu est installé à côté du jeu en cours, puis un lien `current` est
  basculé de façon atomique : une mise à jour interrompue ne change rien. Le
  jeu précédent est conservé.
- À chaque démarrage, la borne vérifie à nouveau le jeu installé (signature,
  puis chaque fichier) et refuse les fichiers de moteur qui n'en font pas
  partie (`signatures.verify`). Sans jeu valide, **elle n'analyse rien** :
  elle affiche `NO VALID SIGNATURES` et n'accepte qu'une clé de mise à jour
  des signatures. Une borne neuve commence donc par en demander une.

Les bases ClamAV (`.cvd`) sont aussi signées par Cisco Talos et vérifiées
par clamd. clamd lit le dossier `clamav` du jeu installé
(`DatabaseDirectory`), et est redémarré quand il change.

## Clés de développement { #development-keys }

`image/build.sh` génère une paire de clés de développement,
`image/update.key` et `image/update.pem` (ignorées par git), quand elles
n'existent pas : l'image fait confiance à `update.pem`, et l'image de test
installe un jeu signé avec `update.key`. Ne les utilisez jamais pour une
borne.

Sur un ordinateur de développement, `pandora-box-signatures publish` sans
`--key` construit un jeu non signé et affiche la configuration
correspondante, avec `verify = false` dans `[signatures]`.
