# Export des journaux vers un SIEM

La borne journalise chaque événement dans un fichier de lignes JSON,
`/var/log/pandora-box/pandora-box.log` : clés insérées et retirées, analyses
et leurs verdicts, fichiers infectés, quarantaine et nettoyage, mises à jour
des signatures et de l'image, clés de maintenance, erreurs. Une borne en
ligne peut envoyer ces entrées à un SIEM (Wazuh, Elastic, Graylog, Splunk,
ou tout serveur syslog), au fur et à mesure.

## Configuration { #configuration }

L'export est désactivé par défaut. Il nécessite le profil d'image `online`
([Construction de l'image](build.md#profile-online)), qui apporte le réseau
et le service `pandora-box-siem`, et la section `[siem]` de la configuration
(`packaging/pandora-box.toml`) :

```toml
[siem]
enabled = true
host = "siem.example.org"
# "tls" (par défaut), "tcp" ou "udp"
transport = "tls"
# 0 : 6514 pour tls, 514 pour tcp et udp
port = 0
# Autorités de certification du certificat du SIEM, pour tls ("" : celles du
# système)
ca = "/usr/share/pandora-box/siem-ca.pem"
# "syslog" (RFC 5424) ou "json"
format = "syslog"
facility = "local0"
```

`pandora-box --check-config` vérifie la section. Les autorités de
certification font partie de l'image : placez-les dans `image/siem-ca.pem`
(PEM), installé sous `/usr/share/pandora-box/siem-ca.pem` à la construction
de l'image.

## Messages { #messages }

Chaque entrée du journal est un message.

**`format = "syslog"`** : un message
[RFC 5424](https://www.rfc-editor.org/rfc/rfc5424) dont l'en-tête vient de
l'entrée, et dont le message est l'entrée JSON elle-même :

```text
<132>1 2026-10-14T10:25:32.123456+00:00 kiosk-01 pandora-box - infected_files - {"timestamp": "2026-10-14T10:25:32.123456+00:00", "level": "WARNING", "kiosk": "kiosk-01", "event": "infected_files", "count": 2}
```

| Champ | Valeur |
|---|---|
| PRI | `facility` et le niveau de l'entrée : DEBUG 7, INFO 6, WARNING 4, ERROR 3 |
| TIMESTAMP | Heure de l'entrée (UTC) |
| HOSTNAME | `kiosk.name` (les caractères autres que l'ASCII imprimable remplacés par `_`) |
| APP-NAME | `pandora-box` |
| MSGID | Nom de l'événement (`device_verdict`, `infected_files`...) |

Sur TCP et TLS, les messages sont délimités par comptage d'octets
(RFC 5425, RFC 6587), comme l'exige syslog sur TLS.

**`format = "json"`** : l'entrée JSON seule, une par ligne sur TCP et TLS,
une par datagramme sur UDP. Cela convient aux récepteurs de lignes JSON
(Logstash `json_lines`, Fluent Bit, Vector) et aux SIEM qui décodent le JSON
d'un message brut.

Chaque entrée contient `timestamp`, `level`, `kiosk` et `event`, puis les
champs de l'événement. Les horodatages viennent de l'horloge de la borne,
synchronisée par NTP dans le profil `online` ([Heure](build.md#time)).

## Transport { #transport }

| `transport` | Port par défaut | |
|---|---|---|
| `tls` | 6514 | Recommandé : le certificat du SIEM est vérifié (autorités de certification de `ca`, nom d'hôte de `host`), TLS 1.2 au minimum |
| `tcp` | 514 | En clair, sur un réseau de confiance uniquement |
| `udp` | 514 | En clair ; un datagramme peut être perdu sans avertissement |

La borne ne s'authentifie pas avec un certificat client.

## Acheminement { #delivery }

Le fichier de journal sert de file d'attente. Le service enregistre la
position de la dernière entrée envoyée (`/var/lib/pandora-box-siem/state.json`) :
quand le SIEM est injoignable, il réessaie (après 1 seconde, puis deux fois
plus longtemps à chaque fois, jusqu'à 1 minute) et envoie, dans l'ordre,
chaque entrée journalisée entre-temps. Il en va de même quand le service ou
la borne redémarre. logrotate déplace le fichier de journal chaque mois : le
service envoie la fin du fichier déplacé avant le nouveau, donc une
interruption de plus d'un mois perd les entrées du mois le plus ancien.

Syslog n'a pas d'accusé de réception : sur TCP et TLS, les entrées envoyées
juste avant une coupure de connexion peuvent être perdues, ou envoyées deux
fois si la coupure survient pendant leur envoi. Sur UDP, rien n'est garanti.

## Le service { #the-service }

`pandora-box-siem.service` (`src/pandora_box/siem.py`) suit le fichier de
journal et envoie ses entrées. Il s'arrête aussitôt quand `[siem]` est
désactivé.

- La borne elle-même n'a pas de réseau (`IPAddressDeny=any`, aucun socket
  IP) : elle ne fait qu'écrire le fichier de journal. Le service tourne sous
  l'utilisateur `pandora-box-siem`, sans privilèges et dans un bac à sable
  systemd, et le pare-feu laisse sortir cet utilisateur (TCP et UDP), en
  plus du service de mise à jour.
- Il lit le fichier de journal grâce au groupe `pandora-box-log`, le groupe
  de `/var/log/pandora-box` et des fichiers de la borne : il ne peut lire ni
  les rapports d'analyse ni la quarantaine (`/var/lib/pandora-box`, mode
  0700).
- Ses messages, connexion perdue puis rétablie, sont dans son journal :
  `journalctl -u pandora-box-siem`.

## Récepteurs { #receivers }

**Wazuh** : le gestionnaire reçoit syslog sur TCP ou UDP, sans TLS, depuis
les adresses qu'il autorise (`<remote>` de `ossec.conf`, `connection`
`syslog`). Avec `format = "json"` et `transport = "tcp"`, chaque entrée
arrive telle quelle au décodeur JSON de Wazuh ; vérifiez le décodage avec
`wazuh-logtest`.

**rsyslog**, sur TLS (paquets `rsyslog` et `rsyslog-gnutls`) :

```text
global(
    DefaultNetstreamDriverCAFile="/etc/rsyslog.d/ca.pem"
    DefaultNetstreamDriverCertFile="/etc/rsyslog.d/siem.pem"
    DefaultNetstreamDriverKeyFile="/etc/rsyslog.d/siem.key"
)
module(load="imtcp" StreamDriver.Name="gtls" StreamDriver.Mode="1"
       StreamDriver.AuthMode="anon")
input(type="imtcp" port="6514")
```

## Développement { #development }

Le service fonctionne aussi en dehors de l'image, avec toute configuration
dont la section `[siem]` est activée, ici vers un récepteur local :

```sh
nc -lk 5514 &
pandora-box-siem --config pandora-box.toml --state /tmp/siem-state.json
```

avec `host = "127.0.0.1"`, `transport = "tcp"` et `port = 5514`. Le test en
machine virtuelle envoie les journaux de la borne à un récepteur syslog du
test ([Test avec QEMU](vm.md#automated-test)).
