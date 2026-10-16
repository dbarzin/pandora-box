# Matériel de référence

Une borne est un Lenovo ThinkCentre Tiny reconditionné, avec un écran tactile
de 7 pouces. L'image fonctionne sur tout ordinateur x86_64 UEFI, mais seul le
matériel ci-dessous est validé : l'image règle le mode de son écran et
autorise son écran tactile (voir [Écran](image.md#screen)).

## Matériel validé { #validated-hardware }

| Élément | Modèle | Remarques |
|---|---|---|
| Ordinateur | Lenovo ThinkCentre M720q Tiny (type de machine 10T7), BIOS M1UKT62A | Core i5-9400T (6 cœurs), Intel UHD Graphics 630, 8 Go, SSD SATA 128 Go |
| Écran | Waveshare 7 pouces HDMI LCD (C), 1024x600, tactile capacitif | HDMI pour l'image, USB pour le contrôleur tactile `0eef:0005` « WS170120 » |

Ce qui a été vérifié : Secure Boot avec la clé de l'image, démarrage et mises
à jour A/B, l'écran (toute l'interface visible), l'écran tactile (confirme un
nettoyage), les clés USB, la détection (EICAR), les mises à jour en ligne des
signatures sur le réseau filaire, les clés de maintenance.

Remarques sur ce matériel :

- **Écran** : le firmware démarre en 1024x768 et le pilote Intel garde ce
  framebuffer ; la ligne de commande du noyau règle le mode de l'écran
  (`video=1024x600M@60`) et la borne réduit la console à cette taille.
- **Écran tactile** : USBGuard autorise son contrôleur tactile par ses
  identifiants, son numéro de série et son port (`1-8`, où il est branché
  dans le boîtier) : `image/mkosi.extra/etc/usbguard/rules.conf`. Pour un
  autre exemplaire ou un autre port, modifiez la règle d'après le fichier
  `usb-devices.txt` d'un export de maintenance, ou d'après le journal d'audit
  d'USBGuard quand il le bloque.
- **Firmware graphique** : l'image n'a pas de firmware pour le circuit
  graphique Intel (le firmware DMC de Coffee Lake) : l'affichage fonctionne,
  sans ses états d'économie d'énergie les plus profonds.
- **Vitesse** : environ 0,5 s par Mo pour un PDF rempli d'images (ClamAV),
  beaucoup moins pour les autres fichiers (ClamAV en prend presque tout le
  temps) ; six fichiers sont analysés en même temps, un par cœur.

Un ordinateur ou un écran absent de cette liste peut demander ses propres
réglages : le mode de son écran, la règle de son écran tactile, le
contrôleur de disque dans l'initrd (`KernelModulesInitrdInclude=` de
`image/mkosi.conf` : NVMe, SATA, Intel VMD, eMMC). Un écran sans tactile
demande une souris, autorisée par sa propre règle USBGuard (voir
[image.md](image.md#mouse-instead-of-a-touchscreen)).

## Boîtier { #enclosure }

Un boîtier de bureau imprimé en 3D, en une seule pièce, d'environ
191 x 184 x 86 mm : l'écran dans la fenêtre de sa face avant inclinée, des
fentes d'aération sur son dos arrondi. Le modèle et ses vues sont dans
[3D/](https://github.com/sourcentis/pandora-box/tree/main/3D), sous licence
CERN Open Hardware Licence v2 - Strongly Reciprocal (CERN-OHL-S v2).

## Installation { #installation }

Les réglages du BIOS du M720q, l'écriture de l'image et le premier démarrage
sont décrits dans [Installation initiale](install.md).
