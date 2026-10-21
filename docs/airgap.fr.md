# Copie vers un serveur (air-gap)

Une borne peut servir de borne de transfert de fichiers, entre les clés des
utilisateurs et un serveur de l'organisation (rsync sur SSH ou SFTP), à la
même insertion :

- **entrée** : une fois une clé analysée et vérifiée, la borne copie ses
  fichiers sains sur le serveur, où ils sont récupérés depuis le réseau
  protégé ;
- **sortie** : les fichiers que le réseau protégé a déposés sur le serveur
  pour cette clé sont analysés, puis les fichiers sains sont écrits sur la
  clé.

La clé elle-même n'est pas nettoyée, sauf avec `airgap.clean` (voir
[Nettoyer la clé](#cleaning-the-key)).

## En bref { #in-short }

1. L'utilisateur insère une clé ; la borne l'analyse.
2. Une clé **non vérifiée** (un fichier n'a pas pu être entièrement analysé)
   est refusée : rien n'est copié, rien n'est écrit dessus.
3. Sinon, la borne lit le jeton de la clé (le fichier caché `.pandora-box`
   à sa racine), ou en écrit un nouveau.
4. Les **fichiers sains** sont copiés dans `<path>/<répertoire>/` sur le
   serveur, le répertoire portant un nom dérivé d'un secret du jeton, avec
   le rapport d'analyse dans `<path>/<répertoire>/.pandora-box/`. Les
   fichiers malveillants et suspects ne sont jamais copiés.
5. Quand la clé revient, son répertoire sur le serveur est **synchronisé** :
   les fichiers nouveaux et modifiés sont copiés, ceux qui ne sont plus sur
   la clé, ou plus sains, sont supprimés du serveur.
6. Ensuite, les fichiers de `<path>/<répertoire>/.pandora-box/outbound/`
   que la clé n'a pas, ou a dans une autre version, sont téléchargés,
   **analysés**, et les fichiers sains sont écrits à la racine de la clé
   (voir [Sortie](#outbound)).

```text
/srv/keys/                                   path sur le serveur, non listable
└── 3f9c…e1a7/                               répertoire de la clé (64 chiffres hexadécimaux)
    ├── docs/report.pdf                      fichiers sains de la clé
    ├── readme.txt
    └── .pandora-box/                        rapports d'analyse, un par insertion
        ├── 7c9e6679-7425-40de-944b-e07fc1f90ae7.json
        ├── 3f2504e0-4f89-41d3-9a0c-0305e82c3301.json
        └── outbound/                        fichiers en attente pour la clé
            └── reponse.pdf
```

## Fonctionnement { #how-it-works }

- **Jeton de la clé.** Le numéro de série d'une clé USB est absent ou
  partagé sur certaines clés : à sa première insertion, la borne écrit dans
  `.pandora-box`, à la racine de son premier système de fichiers, un
  identifiant aléatoire (UUID, écrit dans le rapport d'analyse) et un secret
  aléatoire de 256 bits, signés avec une clé des bornes de l'organisation
  (Ed25519). Le fichier vient de la clé : il est lu comme une donnée hostile
  (taille limitée, format strict, signature vérifiée). Un jeton absent, mal
  formé ou étranger est remplacé par un nouveau : la clé reçoit alors un
  nouveau répertoire sur le serveur. La signature empêche de forger un
  jeton, pas de copier le fichier sur une autre clé. Ce fichier est la seule
  modification apportée à la clé.
- **Répertoire de la clé.** Son nom est le SHA-256 du secret du jeton : la
  borne le calcule à l'insertion de la clé et ne l'écrit, ne l'affiche ni ne
  le garde jamais. Sans la clé, impossible de retrouver le répertoire de ses
  fichiers : ni à partir d'une borne volée, ni avec le compte des bornes sur
  le serveur, qui ne peut pas lister les répertoires (voir
  [Le serveur](#the-server)).
- **Ce qui est copié.** Les fichiers dont le verdict est sain. Chacun est
  d'abord copié par la borne dans un répertoire de transit
  (`airgap.staging`) et comparé au SHA-256 analysé ; un fichier modifié
  depuis l'analyse n'est pas copié. Le répertoire de transit est en mémoire
  (un tmpfs d'au plus la moitié de la mémoire : une copie plus grosse est
  refusée) et il est effacé à la fin de la copie, quel qu'en soit le
  résultat.
- **Rien des clés sur la borne.** Une borne qui copie vers un serveur ne
  garde aucun rapport d'analyse (ils partent sur le serveur, avec les
  fichiers), n'a pas de quarantaine sauf si elle nettoie les clés avec
  `quarantine.enabled`, et son journal
  omet les noms et les empreintes des fichiers, le libellé, le fabricant, le
  modèle et le numéro de série de la clé, et les lignes de l'écran :
  seulement des événements, des verdicts et des compteurs. Voir
  [Une borne volée](#a-stolen-kiosk) pour ce qui reste.
- **Qui copie.** La borne n'a pas de réseau. Le service `pandora-box-airgap`
  tourne sous son propre utilisateur, sans privilèges, et c'est le seul,
  avec les services de mise à jour et de SIEM, que le pare-feu laisse
  sortir. Il lit le répertoire de transit, et rien d'autre de la borne (ni
  les rapports, ni la quarantaine, ni les clés). La borne lui demande de
  copier une clé par son socket Unix.
- **Miroir.** Le répertoire de la clé sur le serveur est une copie des
  fichiers sains de sa dernière analyse vérifiée : les fichiers sont
  comparés par taille et date de modification (2 secondes d'écart au plus :
  vfat) ; un fichier modifié est envoyé sous un nom temporaire puis renommé,
  pour qu'un lecteur ne voie jamais un fichier à moitié écrit. Les rapports
  des analyses précédentes sont gardés.
- **Plusieurs partitions.** Les fichiers de chaque partition vont dans
  `partition-1/`, `partition-2/`...
- **Serveur Windows.** Avec `windows_names = true`, un fichier dont Windows
  refuse le nom (`\ : * ? " < > |`, une espace ou un point final, `CON`,
  `NUL`, `COM1`...), ou qui ne diffère d'un autre que par la casse, n'est
  pas copié et est signalé comme tel.

L'écran montre la copie, puis **Fichiers copiés** avec les fichiers qui
n'ont pas été copiés, dans un sens ou dans l'autre (dangereux, suspects, ou
refusés par le serveur ou par la clé), ou **La copie a échoué** : la clé
peut être insérée à nouveau plus tard.

## Sortie { #outbound }

Le réseau protégé dépose les fichiers destinés à une clé dans le répertoire
`.pandora-box/outbound/` de son répertoire sur le serveur : un opérateur
retrouve ce répertoire grâce à l'identifiant de la clé, dans ses rapports
d'analyse (`key_id`). À chaque insertion :

- **Seulement une clé saine.** Une clé qui contient un fichier que le
  nettoyage supprimerait (un fichier malveillant, ou suspect avec
  `scan.suspicious = "block"`) ne reçoit rien : nettoyez-la sur une borne
  qui nettoie, puis insérez-la à nouveau. Une clé non vérifiée ne reçoit
  rien non plus.
- **Fichiers nouveaux ou modifiés.** La borne indique au service les
  fichiers du premier système de fichiers de la clé (chemin, taille, date
  de modification) ; le service télécharge dans le répertoire de transit
  (en mémoire) les fichiers du répertoire `outbound` que la clé n'a pas, ou
  a dans une autre version. Les fichiers restent sur le serveur : un fichier
  modifié là-bas est copié à nouveau ; supprimez-le une fois livré.
- **Analysés.** Les fichiers téléchargés passent par les mêmes moteurs que
  les fichiers d'une clé : un fichier malveillant, suspect ou pas
  entièrement analysé n'est pas écrit, et il est signalé.
- **Écrits à la racine de la clé**, avec leurs dossiers et leurs dates de
  modification, à la place d'un fichier de même nom. Chaque fichier est
  écrit sous un nom temporaire, comparé au SHA-256 analysé, puis renommé. La
  borne vérifie la place libre de la clé et les limites de son système de
  fichiers : fichiers de 4 Gio au plus en vfat ; noms que FAT, exFAT et NTFS
  refusent (`\ : * ? " < > |`, une espace ou un point final, `CON`,
  `NUL`...) ; noms de 255 caractères au plus ; noms égaux à la casse près
  sur FAT, exFAT et NTFS. Un fichier qui ne peut pas être écrit est signalé,
  jamais renommé. `.pandora-box` à la racine est réservé au jeton de la clé.

`airgap.mode` choisit les sens : `both` (par défaut), `inbound` ou
`outbound`.

## Nettoyer la clé { #cleaning-the-key }

Par défaut, une borne de transfert ne nettoie pas les clés : les fichiers
dangereux ne sont jamais copiés sur le serveur, mais restent sur la clé, qui
ne reçoit alors rien du serveur. Avec `airgap.clean = true`, la borne
nettoie aussi la clé, comme toute borne, à la même insertion :

1. les fichiers sains partent sur le serveur ;
2. les fichiers à supprimer sont affichés, et un appui sur l'écran les
   supprime ; retirer la clé à la place la laisse telle quelle (rien du
   serveur) ;
3. une clé entièrement nettoyée reçoit ensuite les fichiers qui l'attendent
   sur le serveur.

Ce qui est supprimé suit les réglages de toute borne : `scan.suspicious =
"block"` (par défaut) supprime les fichiers suspects avec les fichiers
malveillants, `"warn"` les garde (la clé ne reçoit alors rien du serveur
que s'il reste un fichier malveillant). `quarantine.enabled` garde une copie
des fichiers supprimés sur la borne (`quarantine.folder`) : la seule donnée
des clés qu'elle garde ; réglez-le à `false` pour une borne qui ne garde
rien des clés.

## Configuration { #configuration }

`[airgap]` de `packaging/pandora-box.toml` :

```toml
[airgap]
enabled = true
# "both" (par défaut), "inbound" (clé -> serveur) ou "outbound" (serveur -> clé)
mode = "both"
# Nettoyer aussi la clé, après un appui sur l'écran (voir plus bas)
clean = false
# "rsync" (rsync sur SSH) ou "sftp"
protocol = "sftp"
host = "files.example.org"
port = 0                    # 22
user = "kiosk"
path = "/srv/keys"
windows_names = false       # sftp vers un serveur Windows : voir plus bas
known_hosts = "/etc/pandora-box/airgap-known-hosts"
timeout = 60.0
```

La copie a besoin du réseau : construisez l'image avec le profil `online`
(voir [Construction de l'image](build.md#profile-online)).

### Secrets { #credentials }

`[airgap]` de `image/credentials.toml` (hors de git), installés comme
credentials systemd dans `/etc/credstore` de l'image :

| Clé | Pour | Lue par |
|---|---|---|
| `ssh_key` | la clé SSH privée du compte sur le serveur | le service de copie |
| `token_key` | la clé qui signe les identifiants des clés | la borne |

```sh
ssh-keygen -t ed25519 -N "" -C pandora-box-kiosk -f airgap-ssh-key
openssl genpkey -algorithm ed25519 -out airgap-token.key
```

Collez les clés privées dans `image/credentials.toml`, et la clé SSH
publique (`airgap-ssh-key.pub`) dans le compte du serveur. Utilisez la
**même `token_key` sur toutes les bornes** de l'organisation : une clé
identifiée par une borne garde son répertoire sur les autres. Sans elle, la
borne affiche une erreur au démarrage et refuse de copier.

> [!WARNING]
> Une image construite avec des secrets ne doit pas être publiée : toute
> personne ayant l'image ou le disque de la borne peut les lire.

### Clé d'hôte du serveur { #host-key-of-the-server }

rsync et SFTP ne se connectent qu'à un serveur dont la clé d'hôte figure
dans `airgap.known_hosts` : la borne ne fait jamais confiance à un serveur à
la première connexion. Placez la clé dans `image/airgap-known-hosts`, après
l'avoir vérifiée sur le serveur lui-même (pas seulement par le réseau) :

```sh
ssh-keyscan -t ed25519 files.example.org > image/airgap-known-hosts
ssh-keygen -lf image/airgap-known-hosts    # à comparer avec : ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub sur le serveur
```

Avec un autre port, l'hôte s'écrit `[files.example.org]:2222`.

## Le serveur { #the-server }

Donnez aux bornes un compte qui ne peut écrire que dans le répertoire des
clés, et **qui ne peut pas le lister** : le compte crée le répertoire d'une
clé et l'ouvre par son nom, qui vient de la clé. Avec les secrets d'une
borne volée, les répertoires des clés ne peuvent alors être ni listés ni
retrouvés.

- **SFTP** : un compte limité à SFTP dans un chroot, par exemple dans
  `sshd_config` :

    ```text
    Match User kiosk
        ChrootDirectory /srv/sftp
        ForceCommand internal-sftp
        AllowTcpForwarding no
        X11Forwarding no
    ```

    avec `path = "/keys"`, le répertoire dans le chroot appartenant au
    compte, qui peut y écrire et le traverser, mais pas le lire :

    ```sh
    install -d -m 0300 -o kiosk -g kiosk /srv/sftp/keys
    ```
- **rsync sur SSH** : rsync doit être installé sur le serveur. Limitez la
  clé de la borne à rsync dans son répertoire avec `rrsync` (fourni avec
  rsync), dans `~kiosk/.ssh/authorized_keys` :

    ```text
    command="rrsync /srv/rsync",restrict ssh-ed25519 AAAA... pandora-box-kiosk
    ```

    `rrsync` doit lire son répertoire (il le verrouille) : les clés vont dans
    son sous-répertoire `keys`, que le compte ne peut pas lire, et
    `path = "keys"`, les chemins étant relatifs au répertoire de `rrsync` :

    ```sh
    install -d -m 0700 -o kiosk -g kiosk /srv/rsync
    install -d -m 0300 -o kiosk -g kiosk /srv/rsync/keys
    ```

Seules les bornes suppriment des fichiers dans le répertoire d'une clé :
archivez ou purgez les anciens répertoires sur le serveur.

### Serveur Windows { #windows-server }

Windows fournit un serveur OpenSSH (Windows 10 1809 et Windows Server 2019
ou plus récents) : utilisez `protocol = "sftp"` (Windows n'a pas rsync) et
`windows_names = true`.

1. Installez-le et démarrez-le, dans PowerShell en administrateur :

    ```powershell
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
    Set-Service -Name sshd -StartupType Automatic
    Start-Service sshd
    ```

2. Créez un compte local pour les bornes, **pas un administrateur** (les
   clés des administrateurs sont lues dans
   `C:\ProgramData\ssh\administrators_authorized_keys` à la place), et
   placez la clé publique des bornes dans
   `C:\Users\kiosk\.ssh\authorized_keys`. Sur Windows Server 2025,
   ajoutez-le au groupe local **OpenSSH Users** : sshd n'admet que ce groupe
   et les administrateurs (`AllowGroups`), tout autre compte est refusé avant
   même que sa clé soit lue
   (`Add-LocalGroupMember -Group "OpenSSH Users" -Member kiosk`).
3. Limitez-le à SFTP dans un répertoire, à la fin de
   `C:\ProgramData\ssh\sshd_config`, puis redémarrez le service
   (`Restart-Service sshd`) :

    ```text
    Match User kiosk
        ChrootDirectory C:\PandoraBox
        ForceCommand internal-sftp
        AllowTcpForwarding no
        PermitTTY no
    ```

    avec `path = "/keys"`. Le répertoire `C:\PandoraBox\keys` permet au
    compte de créer le répertoire d'une clé (`AD`), de le traverser (`X`) et
    d'en lire les attributs (`RA`), mais pas de le lister, sans les droits
    hérités de `C:\` (le groupe Users peut le lister) ; dans les répertoires
    des clés, le compte peut modifier :

    ```powershell
    icacls C:\PandoraBox\keys /inheritance:r /grant "SYSTEM:(OI)(CI)F" `
        "Administrators:(OI)(CI)F" "kiosk:(AD,X,RA)" "kiosk:(OI)(CI)(IO)M"
    ```

    Sans chroot, les chemins s'écrivent `/C:/PandoraBox/keys`.
4. Ouvrez le port 22 au réseau des bornes : la règle de pare-feu fournie
   avec OpenSSH n'autorise que le profil réseau **Privé**, et un réseau non
   identifié est Public. Par exemple :
   `Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Profile Domain,Private,Public -RemoteAddress <adresses des bornes>`.
5. La clé d'hôte du serveur, pour `image/airgap-known-hosts` :
   `ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub` sur le
   serveur donne l'empreinte à comparer avec celle de `ssh-keyscan`.

La copie envoie chaque fichier sous un nom temporaire, puis le renomme à la
place de l'ancien (extension SFTP `posix-rename`) ; sur un serveur qui ne la
propose pas, l'ancien fichier est d'abord supprimé, puis le nouveau renommé.

Un serveur Windows peut être testé dans une machine virtuelle : voir
[Tests](#tests).

## Une borne volée { #a-stolen-kiosk }

Ce que contient une borne d'entrée volée, et ce qu'il faut faire :

- **Absent** : les fichiers des clés, leurs noms et leurs empreintes, les
  rapports d'analyse, les répertoires des clés sur le serveur.
- **Encore présent** : le journal du système et le journal d'audit
  d'USBGuard gardent le fabricant, le produit et le numéro de série des
  périphériques USB branchés, tels que le noyau et USBGuard les rapportent ;
  le disque n'est pas chiffré (pas encore de TPM).
- **Les secrets de l'image** : la clé SSH du compte des bornes, et la clé
  qui signe les jetons. Avec eux, on peut se connecter au serveur comme une
  borne, mais ni lister les répertoires des clés ni en retrouver un sans sa
  clé.

Après un vol, retirez la clé publique des bornes du serveur
(`authorized_keys`), puis construisez les images avec une nouvelle clé SSH
et installez-les comme mise à jour sur les autres bornes. Pour révoquer une
borne sans les autres, construisez chaque borne avec son propre
`image/credentials.toml`. Une nouvelle `token_key` rend tous les jetons
invalides : chaque clé reçoit alors un nouveau répertoire, et son ancien
répertoire reste sur le serveur, à archiver ou supprimer.

## Rapport d'analyse { #scan-report }

`actions.airgap` du rapport (voir [Rapport d'analyse](engines.md#scan-report)) :

| Champ | Contenu |
|---|---|
| `status` | `copied`, `failed`, `refused` (clé non vérifiée), ou `skipped` (`mode = "outbound"`) |
| `key_id` | identifiant de la clé (pas le nom de son répertoire) |
| `destination` | répertoire de la clé sur le serveur |
| `copied`, `deleted`, `unchanged` | fichiers envoyés, supprimés du serveur, déjà présents |
| `not_copied` | fichiers qui ne sont pas sains |
| `errors` | fichiers sains qui n'ont pas pu être copiés, et pourquoi |
| `error` | pourquoi la copie a échoué |
| `outbound` | les fichiers du serveur : `status` (`copied`, `nothing`, `refused` quand la clé contient des fichiers dangereux, `failed`), `copied`, `unchanged` (déjà sur la clé), `rejected` (avec la raison), `error` |

Le journal de la borne contient les événements `key_token_written`,
`key_identified`, `key_token_invalid`, `airgap_copied`, `airgap_received`,
`airgap_failed` et `airgap_refused` ; le service journalise `airgap_done` et `airgap_failed`
dans son journal (`journalctl -u pandora-box-airgap`).

## Tests { #tests }

- `tests/airgap/run.sh` copie vers un vrai serveur OpenSSH avec rsync, dans
  un conteneur (SFTP, rsync sur SSH, et rsync limité par `rrsync` comme
  ci-dessus) : une première copie, puis une synchronisation (fichiers
  modifiés, nouveaux et supprimés), une clé d'hôte inconnue refusée, les
  répertoires des clés non listables, le téléchargement des fichiers en
  attente que la clé n'a pas, et le service par son socket.
- `tests/windows/windows.sh` copie vers un Windows Server dans une machine
  virtuelle QEMU, configuré comme ci-dessus (voir plus bas).
- Le [test en machine virtuelle](vm.md) active la copie vers un serveur SFTP
  de test : une clé est copiée, puis insérée à nouveau sans l'un de ses
  fichiers, qui est supprimé du serveur ; la clé garde ses fichiers
  malveillants et reçoit son identifiant. Cette clé infectée ne reçoit rien
  du serveur ; une clé saine reçoit le fichier sain qui l'attend, pas le
  fichier EICAR à côté.

### Serveur Windows en machine virtuelle { #windows-server-in-a-virtual-machine }

La copie vers un serveur Windows est vérifiée sur un vrai Windows Server
2025, dans une machine virtuelle QEMU du conteneur de construction (avec
KVM) :

1. Téléchargez l'ISO de **Windows Server 2025 en évaluation** (en anglais,
   180 jours, sans clé de produit) sur le
   [Microsoft Evaluation Center](https://www.microsoft.com/en-us/evalcenter/download-windows-server-2025).
   Elle reste hors du dépôt.
2. Installez-le une fois, sans intervention (20 à 40 minutes, à suivre par
   VNC sur `localhost:5900`) :

    ```sh
    tests/windows/windows.sh prepare ~/Téléchargements/windows-server-2025-eval.iso
    ```

    `tests/windows/autounattend.xml` installe l'image 1 de l'ISO (Standard,
    Server Core ; `--index 2` pour le bureau), puis `tests/windows/setup.ps1`
    configure OpenSSH comme décrit ci-dessus (le compte `kiosk`, sa clé, SFTP
    seulement dans `C:\PandoraBox`) et éteint la machine. Le disque est
    `tests/windows/work/windows.qcow2` (hors de git).
3. Copiez des clés dessus, autant de fois que nécessaire (chaque passage
   repart du disque installé) :

    ```sh
    tests/windows/windows.sh test
    ```

    Une première copie (les noms que Windows refuse ou confond sont
    signalés), un remplacement, un ajout et une suppression, les dates
    gardées, et un serveur à une autre clé d'hôte refusé.

L'évaluation expire après 180 jours : préparez alors un nouveau disque.
