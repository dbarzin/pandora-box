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
mkdir -p /srv/keys/rrsync/keys
chown -R kiosk: /srv/keys
chown -R restricted: /srv/keys/rrsync
# As docs/airgap.md advises: the account creates and opens the folder of a
# key by its name, but cannot list the folders of the keys (the others may
# go through /srv/keys, to the folder of the restricted account). rrsync
# locks the folder it is restricted to, which it must read: the keys are in
# its subfolder keys
chmod 0311 /srv/keys
chmod 0700 /srv/keys/rrsync
chmod 0300 /srv/keys/rrsync/keys
exec /usr/sbin/sshd -D -e -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
