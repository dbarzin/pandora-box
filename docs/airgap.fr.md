# Copie vers un serveur (air-gap)

Une borne peut servir de borne d'entrée : une fois une clé analysée et
vérifiée, la borne copie ses fichiers sains sur un serveur de l'organisation
(rsync sur SSH, SFTP ou SMB), où ils sont récupérés depuis le réseau protégé.
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
- **Noms SMB.** Un fichier dont un serveur SMB refuse le nom
  (`\ : * ? " < > |`, une espace ou un point final), ou qui ne diffère d'un
  autre que par la casse, n'est pas copié et est signalé comme tel.

L'écran montre la copie, puis **Fichiers copiés sur le serveur** avec les
fichiers qui n'ont pas été copiés (dangereux, suspects ou refusés par le
serveur), ou **La copie vers le serveur a échoué** : la clé n'est alors ni
nettoyée ni copiée, et peut être insérée à nouveau plus tard.

## Configuration { #configuration }

`[airgap]` de `packaging/pandora-box.toml` :

```toml
[airgap]
enabled = true
# "rsync" (rsync sur SSH), "sftp" ou "smb"
protocol = "sftp"
host = "files.example.org"
port = 0                    # 22 pour rsync et sftp, 445 pour smb
user = "kiosk"
path = "/srv/keys"          # smb : un répertoire du partage
share = ""                  # smb : le partage
domain = ""                 # smb : le domaine du compte
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
| `ssh_key` | la clé SSH privée du compte (rsync, sftp) | le service de copie |
| `password` | le mot de passe du compte (smb) | le service de copie |
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
- **SMB** : un partage et un compte avec droit d'écriture ; la borne exige
  SMB 3 avec chiffrement (`server min protocol = SMB3`,
  `server smb encrypt = required` sur Samba).

Seules les bornes suppriment des fichiers dans le répertoire d'une clé :
archivez ou purgez les anciens répertoires sur le serveur.

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

- `tests/airgap/run.sh` copie vers de vrais serveurs dans des conteneurs :
  OpenSSH avec rsync (SFTP, rsync sur SSH, et rsync limité par `rrsync`
  comme ci-dessus) et Samba (SMB 3 chiffré) ; une
  première copie, puis une synchronisation (fichiers modifiés, nouveaux et
  supprimés), une clé d'hôte inconnue et un mauvais mot de passe refusés, et
  le service par son socket.
- Le [test en machine virtuelle](vm.md) active la copie vers un serveur SFTP
  de test : une clé est copiée, puis insérée à nouveau sans l'un de ses
  fichiers, qui est supprimé du serveur ; la clé garde ses fichiers
  malveillants et reçoit son identifiant.
