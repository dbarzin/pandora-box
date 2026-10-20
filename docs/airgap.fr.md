# Copie vers un serveur (air-gap)

Une borne peut servir de borne d'entrée : une fois une clé analysée et
vérifiée, la borne copie ses fichiers sains sur un serveur de l'organisation
(rsync sur SSH ou SFTP), où ils sont récupérés depuis le réseau protégé.
La clé elle-même n'est pas nettoyée et repart avec ses fichiers.

## En bref { #in-short }

1. L'utilisateur insère une clé ; la borne l'analyse.
2. Une clé **non vérifiée** (un fichier n'a pas pu être entièrement analysé)
   est refusée : rien n'est copié, rien n'est écrit dessus.
3. Sinon, la borne lit l'identifiant de la clé (le fichier caché
   `.pandora-box` à sa racine), ou en écrit un nouveau.
4. Les **fichiers sains** sont copiés dans `<path>/<identifiant>/` sur le
   serveur, avec le rapport d'analyse dans
   `<path>/<identifiant>/.pandora-box/`. Les fichiers malveillants et
   suspects ne sont jamais copiés.
5. Quand la clé revient, son répertoire sur le serveur est **synchronisé** :
   les fichiers nouveaux et modifiés sont copiés, ceux qui ne sont plus sur
   la clé, ou plus sains, sont supprimés du serveur.

```text
/srv/keys/                                   path sur le serveur
└── 0f8fad5b-d9cb-469f-a165-70867728950e/    identifiant de la clé
    ├── docs/report.pdf                      fichiers sains de la clé
    ├── readme.txt
    └── .pandora-box/                        rapports d'analyse, un par insertion
        ├── 7c9e6679-7425-40de-944b-e07fc1f90ae7.json
        └── 3f2504e0-4f89-41d3-9a0c-0305e82c3301.json
```

## Fonctionnement { #how-it-works }

- **Identifiant de la clé.** Le numéro de série d'une clé USB est absent ou
  partagé sur certaines clés : à sa première insertion, la borne écrit un
  identifiant aléatoire (UUID) dans `.pandora-box`, à la racine de son
  premier système de fichiers, signé avec une clé des bornes de
  l'organisation (Ed25519). Le fichier vient de la clé : il est lu comme une
  donnée hostile (taille limitée, format strict, signature vérifiée). Un
  jeton absent, mal formé ou étranger est remplacé par un nouveau : la clé
  reçoit alors un nouveau répertoire sur le serveur. La signature empêche de
  forger un identifiant, pas de copier le fichier sur une autre clé. Ce
  fichier est la seule modification apportée à la clé.
- **Ce qui est copié.** Les fichiers dont le verdict est sain. Chacun est
  d'abord copié par la borne dans un répertoire de transit
  (`airgap.staging`) et comparé au SHA-256 analysé ; un fichier modifié
  depuis l'analyse n'est pas copié. Le répertoire de transit est effacé à la
  fin de la copie, quel qu'en soit le résultat.
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

L'écran montre la copie, puis **Fichiers copiés sur le serveur** avec les
fichiers qui n'ont pas été copiés (dangereux, suspects ou refusés par le
serveur), ou **La copie vers le serveur a échoué** : la clé n'est alors ni
nettoyée ni copiée, et peut être insérée à nouveau plus tard.

## Configuration { #configuration }

`[airgap]` de `packaging/pandora-box.toml` :

```toml
[airgap]
enabled = true
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
clés.

- **SFTP** : un compte limité à SFTP dans un chroot, par exemple dans
  `sshd_config` :

    ```text
    Match User kiosk
        ChrootDirectory /srv/sftp
        ForceCommand internal-sftp
        AllowTcpForwarding no
        X11Forwarding no
    ```

    avec `path = "/keys"` (le répertoire dans le chroot, accessible en
    écriture au compte).
- **rsync sur SSH** : rsync doit être installé sur le serveur. Limitez la
  clé de la borne à rsync dans son répertoire avec `rrsync` (fourni avec
  rsync), dans `~kiosk/.ssh/authorized_keys` :

    ```text
    command="rrsync /srv/keys",restrict ssh-ed25519 AAAA... pandora-box-kiosk
    ```

    puis `path = "."`, les chemins étant relatifs au répertoire de `rrsync`.

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

    avec `path = "/keys"`, le répertoire `C:\PandoraBox\keys` étant
    accessible en écriture au compte. Sans chroot, les chemins s'écrivent
    `/C:/PandoraBox/keys`.
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

## Rapport d'analyse { #scan-report }

`actions.airgap` du rapport (voir [Rapport d'analyse](engines.md#scan-report)) :

| Champ | Contenu |
|---|---|
| `status` | `copied`, `failed`, ou `refused` (clé non vérifiée) |
| `key_id` | identifiant de la clé, le nom de son répertoire |
| `destination` | répertoire de la clé sur le serveur |
| `copied`, `deleted`, `unchanged` | fichiers envoyés, supprimés du serveur, déjà présents |
| `not_copied` | fichiers qui ne sont pas sains |
| `errors` | fichiers sains qui n'ont pas pu être copiés, et pourquoi |
| `error` | pourquoi la copie a échoué |

Le journal de la borne contient les événements `key_token_written`,
`key_identified`, `key_token_invalid`, `airgap_copied`, `airgap_failed` et
`airgap_refused` ; le service journalise `airgap_done` et `airgap_failed`
dans son journal (`journalctl -u pandora-box-airgap`).

## Tests { #tests }

- `tests/airgap/run.sh` copie vers un vrai serveur OpenSSH avec rsync, dans
  un conteneur (SFTP, rsync sur SSH, et rsync limité par `rrsync` comme
  ci-dessus) : une première copie, puis une synchronisation (fichiers
  modifiés, nouveaux et supprimés), une clé d'hôte inconnue refusée, et le
  service par son socket.
- `tests/windows/windows.sh` copie vers un Windows Server dans une machine
  virtuelle QEMU, configuré comme ci-dessus (voir plus bas).
- Le [test en machine virtuelle](vm.md) active la copie vers un serveur SFTP
  de test : une clé est copiée, puis insérée à nouveau sans l'un de ses
  fichiers, qui est supprimé du serveur ; la clé garde ses fichiers
  malveillants et reçoit son identifiant.

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
