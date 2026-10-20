#!/bin/sh
# Authorize the key of the kiosk (made by the keygen service), keep the host
# key where the kiosk can read it (known_hosts), then serve.
#   kiosk       rsync over SSH and SFTP, in /srv/keys
#   restricted  the same key, which may only run rsync in /srv/keys/rrsync
#               (rrsync, as docs/airgap.md advises)
set -eu
key=$(cat /secrets/id_ed25519.pub)
for user in kiosk restricted; do
    install -d -m 0700 -o "$user" -g "$user" "/home/$user/.ssh"
done
printf '%s\n' "$key" >/home/kiosk/.ssh/authorized_keys
printf 'command="rrsync /srv/keys/rrsync",restrict %s\n' "$key" \
    >/home/restricted/.ssh/authorized_keys
chown kiosk: /home/kiosk/.ssh/authorized_keys
chown restricted: /home/restricted/.ssh/authorized_keys
chmod 0600 /home/*/.ssh/authorized_keys
ssh-keygen -A >/dev/null
printf 'sshd %s\n' "$(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)" >/secrets/known_hosts
mkdir -p /srv/keys/rrsync
chown -R kiosk: /srv/keys
chown -R restricted: /srv/keys/rrsync
exec /usr/sbin/sshd -D -e -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
