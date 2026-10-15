# Configuration

La borne lit sa configuration dans `/etc/pandora-box/pandora-box.toml` (un
autre fichier avec `--config`, ou la variable d'environnement
`PANDORA_BOX_CONFIG`). Chaque clé est facultative : une clé absente prend sa
valeur par défaut. La configuration est vérifiée au démarrage, et une clé
inconnue ou une valeur invalide est refusée : la borne ne démarre pas avec
une configuration qu'elle ne comprend pas.

Dans l'image, le système de fichiers racine est en lecture seule : la
configuration fait partie de l'image, reprise de `packaging/pandora-box.toml`
à la construction de l'image. Pour la modifier, éditez ce fichier,
construisez une nouvelle image et installez-la comme une mise à jour (voir
[Construction de l'image](build.md) et
[Mises à jour du système](update.md)).

Vérifier une configuration, ses signatures, ses moteurs et son thème sans
démarrer la borne :

```sh
pandora-box --config pandora-box.toml --check-config
```

## Sections { #sections }

| Section | Réglages |
|---|---|
| `[kiosk]` | Nom de la borne dans les journaux, interface (`framebuffer`, `curses`, `console`), mode FAKE_SCAN |
| `[display]` | Thème, langue, framebuffer, retour à l'écran d'attente : voir [Interface](interface.md) |
| `[device]` | Point de montage, systèmes de fichiers autorisés, éjection |
| `[scan]` | Processus d'analyse en parallèle, délai par fichier, politiques `suspicious` et `on_error`, bac à sable |
| `[limits]` | Taille maximale d'un fichier, nombre de fichiers, profondeur des dossiers |
| `[policy]` | Nombre de moteurs nécessaires pour un verdict malveillant |
| `[engines.*]` | Chaque moteur de détection : voir [Moteurs de détection](engines.md) |
| `[signatures]` | Jeux de signatures et leur vérification : voir [Signatures](signatures.md) |
| `[updates]` | Mises à jour en ligne des signatures et de l'image : voir [Signatures](signatures.md#online-updates) et [Mises à jour du système](update.md#online) |
| `[siem]` | Export des journaux vers un SIEM : voir [Export vers un SIEM](siem.md) |
| `[report]`, `[quarantine]`, `[logging]` | Dossiers des rapports d'analyse et de la quarantaine, fichier et niveau de journal : voir [Journaux](logs.md) |

## Référence { #reference }

La configuration de l'image,
[`packaging/pandora-box.toml`](https://github.com/sourcentis/pandora-box/blob/main/packaging/pandora-box.toml),
avec chaque clé, sa valeur par défaut et sa signification (commentaires en
anglais) :

```toml
--8<-- "packaging/pandora-box.toml"
```
