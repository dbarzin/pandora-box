# Mises à jour du système

Le système d'une borne est en lecture seule : il est mis à jour en entier,
par une nouvelle image, jamais fichier par fichier. Le disque a deux
emplacements système (A/B) : la mise à jour est écrite dans l'emplacement
libre pendant que la borne fonctionne, et la version précédente reste en
place tant que la nouvelle n'a pas démarré correctement. La configuration et
le thème font partie de l'image : les modifier est aussi une mise à jour.

Les signatures sont mises à jour séparément, bien plus souvent : voir
[Signatures](signatures.md).

## En bref { #in-short }

```sh
image/build.sh                    # nouvelle version, mêmes clés et même profil que la borne
image/package-update.sh image/mkosi.output/pandora-box.raw update image/update.key
cp -r update /media/$USER/KEY/pandora-box-image
```

Insérez la clé dans la borne : elle installe la mise à jour et redémarre sur
la nouvelle version.

## Construire une mise à jour { #building-an-update }

Construisez une nouvelle version avec les mêmes clés que l'image installée
(sa version est l'heure de construction : plus récente que celle en cours),
et avec le même profil, puis empaquetez-la :

```sh
image/build.sh
image/package-update.sh image/mkosi.output/pandora-box.raw update image/update.key
```

`update/` contient les partitions de l'emplacement A de la nouvelle image
(compressées en xz, le format que décompresse `systemd-sysupdate` de
Debian 13), nommées d'après leur UUID, l'image noyau unifiée, et un
manifeste signé avec la clé de mise à jour (le format des jeux de
signatures, contenu `image`, numéro de série = version). Environ 215 Mo,
dont 40 Mo pour l'image noyau.

Deux clés protègent la mise à jour : la clé de mise à jour décide de ce qui
est installé, la clé Secure Boot décide de ce qui peut démarrer (l'image
noyau est signée et contient le hash racine de son emplacement). Une mise à
jour construite avec d'autres clés est refusée, ou ne démarre pas.

## Installation par clé USB { #installing-from-a-usb-key }

Copiez `update/` sous le nom `pandora-box-image/` à la racine d'une clé USB
(vfat, exFAT, NTFS ou ext4) et insérez-la dans la borne
(`updates.image_from_devices`, activé par défaut).

La borne vérifie la signature, que la version est plus récente que celle en
cours, et chaque fichier, les copie dans `/var/lib/pandora-box-image`, puis
`systemd-sysupdate` écrit les partitions dans l'emplacement libre et l'image
noyau sur l'ESP avec 3 essais de démarrage (`pandora-box_2+3-0.efi`), et la
borne redémarre. La version en cours n'est jamais écrasée ; la clé n'est pas
analysée.

Une même clé peut contenir une demande de maintenance, un jeu de signatures
et une mise à jour de l'image : la borne les traite dans cet ordre, chacun
vérifié séparément ; la mise à jour de l'image vient en dernier, puisqu'elle
redémarre la borne.

## En ligne { #online }

Avec le profil `online` ([Construction de l'image](build.md#profile-online))
et `updates.image_url`, l'URL d'un dossier `update/` publié,
`pandora-box-update.service` télécharge une mise à jour plus récente sous
son utilisateur sans privilèges (chaque fichier vérifié), puis l'installe en
root sans réseau (`pandora-box-image install`), avec les mêmes vérifications
qu'une clé de mise à jour. La borne redémarre sur la nouvelle version quand
elle est au repos (entre deux clés) : une analyse n'est jamais interrompue.

## Si la nouvelle version échoue { #if-the-new-version-fails }

La nouvelle version est conservée (`systemd-bless-boot`) une fois
`boot-complete.target` atteint, ce qui exige que la borne soit prête
(`Type=notify`). Un démarrage qui échoue redémarre : panique du noyau
(`panic=10`), échec dans l'initrd (son shell de secours redémarre,
`image/mkosi.images/initrd/`), ou pas de `boot-complete.target` en 15
minutes. Après 3 essais ratés, systemd-boot redémarre sur la version
précédente ; les données (`/var` : rapports, quarantaine, journaux,
signatures) sont partagées par les versions.

`boot-entries.txt` et `boots.txt` d'une
[clé de maintenance](logs.md#maintenance-device) montrent les versions
installées et les démarrages.

## Ce qui n'est pas mis à jour { #what-is-not-updated }

L'ESP ne fait pas partie des mises à jour de l'image : systemd-boot, ses
réglages et les clés Secure Boot enrôlées dans le firmware restent tels
qu'installés. Changer la clé Secure Boot demande une nouvelle
[installation](install.md).
