# Log export to a SIEM

The kiosk logs every event to a JSON lines file,
`/var/log/pandora-box/pandora-box.log`: devices inserted and removed, scans
and their verdicts, infected files, quarantine and cleaning, signature and
image updates, maintenance devices, errors. An online kiosk can send these
entries to a SIEM (Wazuh, Elastic, Graylog, Splunk, or any syslog server), as
they are logged.

## Configuration

The export is off by default. It needs the image profile `online`
([image.md](image.md#profile-online)), which brings the network and the
`pandora-box-siem` service, and the `[siem]` section of the configuration
(`packaging/pandora-box.toml`):

```toml
[siem]
enabled = true
host = "siem.example.org"
# "tls" (default), "tcp" or "udp"
transport = "tls"
# 0: 6514 for tls, 514 for tcp and udp
port = 0
# Certificate authorities of the SIEM certificate, for tls ("": those of the
# system)
ca = "/usr/share/pandora-box/siem-ca.pem"
# "syslog" (RFC 5424) or "json"
format = "syslog"
facility = "local0"
```

`pandora-box --check-config` checks the section. The certificate
authorities are part of the image: put them in `image/siem-ca.pem` (PEM),
installed as `/usr/share/pandora-box/siem-ca.pem` when the image is built.

## Messages

Each log entry is one message.

**`format = "syslog"`**: an [RFC 5424](https://www.rfc-editor.org/rfc/rfc5424)
message whose header comes from the entry, and whose message is the JSON
entry itself:

```text
<132>1 2026-10-14T10:25:32.123456+00:00 kiosk-01 pandora-box - infected_files - {"timestamp": "2026-10-14T10:25:32.123456+00:00", "level": "WARNING", "kiosk": "kiosk-01", "event": "infected_files", "count": 2}
```

| Field | Value |
|---|---|
| PRI | `facility` and the level of the entry: DEBUG 7, INFO 6, WARNING 4, ERROR 3 |
| TIMESTAMP | Time of the entry (UTC) |
| HOSTNAME | `kiosk.name` (characters other than printable ASCII replaced by `_`) |
| APP-NAME | `pandora-box` |
| MSGID | Name of the event (`device_verdict`, `infected_files`...) |

Over TCP and TLS, the messages are framed by octet counting (RFC 5425,
RFC 6587), as syslog over TLS requires.

**`format = "json"`**: the JSON entry alone, one per line over TCP and TLS,
one per datagram over UDP. This suits receivers of JSON lines (Logstash
`json_lines`, Fluent Bit, Vector) and SIEMs that decode the JSON of a raw
message.

Every entry holds `timestamp`, `level`, `kiosk` and `event`, then the fields
of the event. The timestamps come from the clock of the kiosk, synchronized
by NTP in the profile `online` ([image.md](image.md#time)).

## Transport

| `transport` | Default port | |
|---|---|---|
| `tls` | 6514 | Recommended: the certificate of the SIEM is verified (certificate authorities of `ca`, host name of `host`), TLS 1.2 at least |
| `tcp` | 514 | Clear text, on a trusted network only |
| `udp` | 514 | Clear text; a datagram may be lost without notice |

The kiosk does not authenticate itself with a client certificate.

## Delivery

The log file is the queue. The service saves the position of the last entry
sent (`/var/lib/pandora-box-siem/state.json`): when the SIEM cannot be
reached, it tries again (after 1 second, then twice longer each time, up to
1 minute) and sends every entry logged meanwhile, in order. The same applies
when the service or the kiosk restarts. logrotate moves the log file every
month: the service sends the end of the moved file before the new one, so an
outage longer than a month loses the entries of the oldest month.

Syslog has no acknowledgment: over TCP and TLS, the entries sent just before
a connection breaks may be lost, or sent twice when it is broken while they
are sent. Over UDP, nothing is guaranteed.

## The service

`pandora-box-siem.service` (`src/pandora_box/siem.py`) follows the log file
and sends its entries. It exits at once when `[siem]` is disabled.

- The kiosk itself has no network (`IPAddressDeny=any`, no IP socket): it
  only writes the log file. The service runs as the `pandora-box-siem` user,
  without privileges and in a systemd sandbox, and the firewall lets that
  user out (TCP and UDP) besides the update service.
- It reads the log file through the `pandora-box-log` group, the group of
  `/var/log/pandora-box` and of the files of the kiosk: it can read neither
  the scan reports nor the quarantine (`/var/lib/pandora-box`, mode 0700).
- Its messages, connection lost and back, are in its journal:
  `journalctl -u pandora-box-siem`.

## Receivers

**Wazuh**: the manager receives syslog over TCP or UDP, without TLS, from the
addresses it allows (`<remote>` of `ossec.conf`, `connection` `syslog`). With
`format = "json"` and `transport = "tcp"`, each entry reaches the JSON
decoder of Wazuh as is; check the decoding with `wazuh-logtest`.

**rsyslog**, over TLS (packages `rsyslog` and `rsyslog-gnutls`):

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

## Development

The service runs outside of the image too, with any configuration whose
`[siem]` is enabled, here towards a local receiver:

```sh
nc -lk 5514 &
pandora-box-siem --config pandora-box.toml --state /tmp/siem-state.json
```

with `host = "127.0.0.1"`, `transport = "tcp"` and `port = 5514`. The
virtual machine test sends the logs of the kiosk to a syslog receiver of the
test ([image.md](image.md#automated-test-in-a-virtual-machine)).
