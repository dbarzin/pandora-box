# Construction de l'image

La borne est une image système complète, construite avec
[mkosi](https://github.com/systemd/mkosi) à partir des paquets Debian 13
(trixie). Tout s'exécute dans un conteneur Debian 13 (`image/Dockerfile` :
mkosi, QEMU, OVMF) : l'ordinateur de construction n'a besoin que de Docker.

> [!WARNING]
> L'image et les jeux de signatures ne sont signés qu'avec des clés de
> développement. L'image de production n'a pas de jeu de signatures : la
> borne n'analyse rien tant qu'une clé de mise à jour des signatures n'a pas
> été insérée (voir [Signatures](signatures.md)).
> Voir les tâches de la phase 2 dans la [feuille de route](https://github.com/sourcentis/pandora-box/blob/main/ROADMAP.md#phase-2--minimal-hardened-image).

## En bref { #in-short }

```sh
image/build.sh                     # borne hors ligne
image/build.sh --profile online    # borne en réseau : mises à jour en ligne, SIEM, NTP
```

L'image est `image/mkosi.output/pandora-box.raw` : écrivez-la sur le disque
de la borne ([Installation initiale](install.md)), ou testez-la d'abord dans
une machine virtuelle ([Test avec QEMU](vm.md)).

## Prérequis { #requirements }

Docker, environ 5 Go d'espace disque, un accès réseau (paquets Debian et
wheels Python).

## Avant la construction { #before-the-build }

L'image est en lecture seule : tout ce qui suit en fait partie, et le
modifier implique de construire une nouvelle image, puis de l'installer
comme une [mise à jour](update.md).

| Quoi | Où |
|---|---|
| Configuration de la borne | `packaging/pandora-box.toml` (voir [Configuration](configuration.md)) |
| Thème de l'interface | `image/theme/` (voir [Interface](interface.md)) |
| Mode de l'écran | `video=` sur la ligne de commande du noyau, `image/mkosi.conf` (voir [Écran](image.md#screen)) |
| Écran tactile autorisé par USBGuard | `image/mkosi.extra/etc/usbguard/rules.conf` (voir [Périphériques USB](image.md#usb-devices)) |
| Contrôleur de disque d'un autre ordinateur | `KernelModulesInitrdInclude=` de `image/mkosi.conf` (voir [Image noyau unifiée](image.md#unified-kernel-image)) |
| Autorités de certification du SIEM | `image/siem-ca.pem` (voir [SIEM](siem.md)) |
| Auth-Key abuse.ch, profil `online` | `image/credentials.toml` (voir [Profil online](#profile-online)) |
| Serveurs NTP, profil `online` | voir [Heure](#time) |

### Clés { #keys }

L'image est signée avec `image/mkosi.key` et `image/mkosi.crt`
(systemd-boot, image noyau unifiée, hash racine dm-verity) : le firmware
d'une borne ne démarre qu'une image signée avec cette clé. Elle fait
confiance aux jeux de signatures, aux mises à jour de l'image et aux
demandes de maintenance signés avec `image/update.key` : sa clé publique
`image/update.pem` est installée dans l'image.

Quand elles n'existent pas, `image/build.sh` génère une paire de
**développement** pour chacune, valable 10 ans, qui ne quitte jamais
l'ordinateur de construction (ignorée par git). Conservez-les : une borne
n'accepte que les mises à jour signées avec les clés de son image. Une borne
ne doit faire confiance qu'aux clés de publication du projet, qui ne sont
pas encore définies : elles seront gardées hors du dépôt, et mkosi peut
signer avec une clé dans un jeton matériel
(`--secure-boot-key-source=provider:pkcs11`).

## Construction { #build }

```sh
image/build.sh                  # image de production
image/build.sh --profile online # image de production en réseau
image/build.sh --profile test   # image de test, pour la machine virtuelle uniquement
```

L'image est écrite dans `image/mkosi.output/` : `pandora-box.raw` (ou
`pandora-box-test.raw`, une image disque GPT), l'image noyau unifiée
(`.efi`) et la liste des paquets installés (`.manifest`). La première
construction prend quelques minutes ; les suivantes réutilisent les paquets
installés (construction incrémentale de mkosi, en cache dans le volume
Docker `pandora-box-mkosi`) et prennent moins d'une minute.

Le cache n'est pas reconstruit quand `image/requirements.txt` ou
`image/mkosi.prepare.chroot` changent : reconstruisez-le avec
`image/build.sh -f` (`docker volume rm pandora-box-mkosi` le supprime
complètement).

La version de l'image est la date et l'heure de sa construction, en UTC,
comme le numéro de série d'un jeu de signatures : `20261005143700`
(`--image-version=N` la remplace). Elle figure dans `/etc/os-release`
(`IMAGE_VERSION`), dans les étiquettes de ses partitions et dans le nom de
son image noyau unifiée. Une mise à jour doit avoir une version plus récente
que celle en cours.

## Profil online { #profile-online }

Sans profil, l'image n'a aucune configuration réseau.
`--profile online` ajoute les mises à jour en ligne des signatures depuis
leurs sources (voir [Signatures](signatures.md#online-updates)) :
systemd-networkd (DHCP sur le réseau filaire), systemd-resolved,
systemd-timesyncd, freshclam, le timer `pandora-box-update` et un pare-feu
qui ne laisse sortir que le service de mise à jour, l'export des journaux
vers un SIEM (voir [SIEM](siem.md)) et NTP (`image/mkosi.profiles/online/`).
Son `postinst.chroot` active `[updates]` dans la configuration de la borne,
et installe les identifiants de `image/credentials.toml` (hors de git) dans
`/etc/credstore`.

```sh
cp image/credentials.toml.example image/credentials.toml   # puis l'Auth-Key abuse.ch
image/build.sh --profile online
```

> [!WARNING]
> Une image construite avec des identifiants **ne doit pas être publiée** :
> quiconque possède l'image ou le disque de la borne peut les lire.

### Heure { #time }

L'horloge de la borne date ses journaux et ses rapports d'analyse, et décide
si les certificats des serveurs de mise à jour et du SIEM sont valides. Une
borne en ligne la synchronise avec **systemd-timesyncd**, à partir des
serveurs NTP de `NTP=` dans
`image/mkosi.profiles/online/mkosi.extra/usr/lib/systemd/timesyncd.conf.d/50-pandora-box.conf`
(vide par défaut), sinon de ceux que fournit le serveur DHCP, sinon du pool
Debian (`FallbackNTP=`). Pour utiliser les serveurs de l'organisation,
indiquez-les là avant de construire l'image :

```ini
[Time]
NTP=ntp1.example.org ntp2.example.org
```

Seul systemd-timesyncd peut envoyer des requêtes NTP (UDP 123) à travers le
pare-feu ; `timedatectl timesync-status` affiche le serveur utilisé. NTP
n'est pas authentifié (timesyncd n'a pas NTS) : quelqu'un placé sur le
chemin des requêtes peut décaler l'horloge, préférez donc des serveurs NTP
du réseau local. Sans le profil, la borne garde l'heure de son horloge
matérielle (UTC).

## Dépendances { #dependencies }

Les dépendances d'exécution sont figées avec leurs empreintes dans
`image/requirements.txt`. Après un changement des dépendances dans
`pyproject.toml`, régénérez-le avec `image/lock-requirements.sh`, puis
reconstruisez le cache (`image/build.sh -f`).

## Fichiers { #files }

| Chemin | Rôle |
|---|---|
| `image/mkosi.conf` | Définition de l'image : Debian 13, x86_64, systemd-boot, image noyau unifiée (UKI), paquets |
| `image/mkosi.repart/` | Partitions : ESP, deux emplacements système (système de fichiers racine en lecture seule, son hash dm-verity et sa signature), partition de données |
| `image/mkosi.images/initrd/` | L'initrd : l'initrd par défaut de mkosi, qui redémarre au lieu d'ouvrir un shell de secours, sans les fichiers dont une racine en lecture seule n'a pas besoin |
| `image/mkosi.prepare.chroot` | Dépendances d'exécution dans un environnement virtuel (`/usr/lib/pandora-box`), d'après `image/requirements.txt` (figées avec leurs empreintes) |
| `image/mkosi.build.chroot` | Wheel de pandora-box, construite depuis le dépôt sans réseau |
| `image/mkosi.postinst.chroot` | Installe pandora-box, sa configuration, son service systemd, ses fichiers tmpfiles et logrotate (`packaging/`), et les réglages de clamd |
| `image/mkosi.extra/` | Fichiers copiés dans l'image : presets systemd (unités activées et désactivées), `/etc/fstab`, agrandissement de la partition de données (`/usr/lib/repart.d/`), mises à jour A/B (`/usr/lib/sysupdate.d/`, évaluation du démarrage), politique USBGuard, réglages du noyau (`sysctl.d`), pare-feu (`nftables.conf`), bac à sable de clamd |
| `image/mkosi.profiles/online/` | Profil `online` |
| `image/mkosi.profiles/test/` | Profil de test, pour les tests en machine virtuelle uniquement |
| `image/vm/` | Tests en machine virtuelle : corpus, pilote QEMU, test automatisé |
| `image/build.sh`, `image/vm.sh` | Construire et exécuter dans le conteneur |
| `image/build-test.sh`, `image/package-update.sh` | Construire les images du test en machine virtuelle ; construire une mise à jour signée de l'image |
| `image/maintenance-key.sh` | Préparer une clé de maintenance |

Ce que contient l'image et comment elle est protégée : voir
[Fonctionnement de l'image](image.md).
