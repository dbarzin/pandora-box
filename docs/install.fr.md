# Installation initiale

Une borne s'installe une seule fois : le BIOS est configuré, l'image est
écrite sur son disque, Secure Boot est verrouillé sur la clé de l'image au
premier démarrage, puis un premier jeu de signatures est installé depuis une
clé USB. Ensuite, elle n'est plus mise à jour que par des clés USB signées
ou en ligne ([Maintenance](update.md)).

## En bref { #in-short }

1. Construisez l'image : [Construction de l'image](build.md).
2. [Configurez le BIOS](#bios-configuration) : mot de passe superviseur,
   Secure Boot en mode configuration, démarrage sur le disque interne
   uniquement.
3. [Écrivez l'image](#writing-the-image) sur le disque de la borne.
4. [Premier démarrage](#first-boot) : l'image enrôle sa clé Secure Boot ;
   vérifiez que Secure Boot est en mode **User**.
5. [Insérez un premier jeu de signatures](#first-signature-set) : jusque-là,
   la borne affiche `NO VALID SIGNATURES` et n'analyse rien.
6. [Vérifiez la borne](#checks) avec une clé de test et une clé de
   maintenance.

Les réglages ci-dessous sont ceux du matériel validé, un Lenovo ThinkCentre
M720q Tiny (voir [Matériel](hardware.md)).

## Configuration du BIOS { #bios-configuration }

Les réglages du ThinkCentre M720q (touche **F1** à la mise sous tension).
Les noms sont ceux du BIOS Lenovo de cette génération et peuvent varier
légèrement selon la version ; le but de chaque réglage compte plus que son
nom exact.

### Sécurité { #security }

| Réglage | Valeur | Pourquoi |
|---|---|---|
| Security → Supervisor Password | défini, conservé par l'exploitant | personne ne change l'ordre de démarrage, Secure Boot ou les réglages ci-dessous |
| Security → Power-On Password | aucun | la borne redémarre seule après une coupure de courant |
| Security → Secure Boot → Secure Boot | Enabled | le firmware ne démarre que le chargeur de démarrage signé de l'image |
| Security → Secure Boot → Secure Boot Mode | Custom, puis **Reset to Setup Mode** (ou Clear All Secure Boot Keys) | à son premier démarrage, l'image enrôle son propre certificat (`loader/keys/auto`) : l'ordinateur ne démarre ensuite que des images PandoraBox |
| Security → Device Guard / Intel TXT | par défaut | non utilisé |
| Security → Chassis Intrusion Detection | Enabled, si le boîtier a le contacteur | une borne ouverte est signalée au démarrage suivant |

N'enrôler que le certificat de l'image refuse aussi les option ROM signées
par Microsoft : sans conséquence avec le circuit graphique Intel intégré du
M720q, mais un ordinateur qui a besoin d'une carte graphique ou d'un
contrôleur de disque additionnel ne démarrerait pas.

### Démarrage { #startup }

| Réglage | Valeur | Pourquoi |
|---|---|---|
| Startup → Boot Mode | UEFI only | l'image ne démarre qu'en mode UEFI |
| Startup → Primary Boot Sequence | le SSD interne uniquement | la borne ne démarre jamais sur une clé USB insérée par un utilisateur |
| Startup → USB Boot | Disabled | idem |
| Startup → PXE / Network Boot | Disabled | pas de démarrage par le réseau |
| Startup → Boot Menu (F12) | Disabled, si proposé | aucun autre périphérique de démarrage choisi à la mise sous tension (le mot de passe superviseur le protège sinon) |
| Startup → Fast Boot | par défaut | |

### Périphériques et réglages avancés { #devices-and-advanced-settings }

| Réglage | Valeur | Pourquoi |
|---|---|---|
| Advanced → CPU Setup → Intel Virtualization Technology for Directed I/O (VT-d) | Enabled | l'IOMMU protège la mémoire contre les accès DMA des périphériques (le noyau l'utilise en mode strict) |
| Devices → Network Setup → Wake on LAN | Disabled | |
| Devices → USB Setup | les ports qui ne servent ni aux utilisateurs ni à l'écran tactile désactivés, si le BIOS le permet port par port | moins de points d'entrée (durcissement physique) |
| Devices → Audio / Serial / Card reader | Disabled s'ils sont présents | non utilisés |

### Alimentation { #power }

| Réglage | Valeur | Pourquoi |
|---|---|---|
| Power → After Power Loss | Power On | la borne redémarre après une coupure de courant |

## Écriture de l'image { #writing-the-image }

Le SSD est écrit une seule fois, depuis un autre ordinateur (dans un boîtier
USB) :

```sh
lsblk -o NAME,SIZE,MODEL,TRAN          # le disque du SSD, par exemple /dev/sdX
sudo umount /dev/sdX?*                 # si des partitions ont été montées automatiquement
sudo dd if=image/mkosi.output/pandora-box.raw of=/dev/sdX bs=4M conv=fsync status=progress
```

> [!CAUTION]
> Vérifiez deux fois le nom du disque : `dd` écrase tout le disque.

L'image peut être écrite sur un disque de n'importe quelle taille : à chaque
démarrage, la partition de données (`/var`) s'agrandit pour remplir le
disque.

## Premier démarrage { #first-boot }

Remettez le SSD dans la borne et démarrez-la. Avec le firmware en mode
configuration de Secure Boot, systemd-boot enrôle le certificat de l'image à
la place des clés Microsoft (PK, KEK et db, depuis `loader/keys/auto/` sur
l'ESP), puis la borne démarre. À partir de là, l'ordinateur ne démarre que
des images signées avec la même clé.

Après ce premier démarrage, vérifiez dans le BIOS que Secure Boot est actif :
il affiche le mode **User**. Pour rendre l'ordinateur à un autre usage :
Security → Secure Boot → **Restore Factory Keys**.

Le chargeur de démarrage n'a pas de menu et ne lit pas le clavier : il n'y a
aucun moyen de choisir un autre système ou d'ouvrir une session sur la
borne.

## Premier jeu de signatures { #first-signature-set }

Une borne neuve n'a pas de jeu de signatures : elle affiche
`NO VALID SIGNATURES` et n'accepte qu'une clé de mise à jour des signatures.
Préparez-en une sur l'ordinateur qui détient la clé de mise à jour, et
insérez-la (voir [Signatures](signatures.md#updating-a-kiosk)) :

```sh
export ABUSECH_AUTH_KEY=...
publish/publish.sh /srv/pandora-box/pandora-box-signatures image/update.key
cp -r /srv/pandora-box/pandora-box-signatures /media/$USER/KEY/
```

La borne vérifie et installe le jeu, charge ses moteurs, demande de retirer
la clé, puis attend une clé à analyser. Une borne construite avec le profil
`online` et branchée au réseau télécharge aussi ses signatures elle-même,
5 minutes après le démarrage
([Mises à jour en ligne](signatures.md#online-updates)).

## Vérifications { #checks }

- Insérez une clé contenant le
  [fichier de test EICAR](https://www.eicar.org/download-anti-malware-testfile/) :
  la borne le signale, et le supprime après un appui sur l'écran.
- L'écran tactile confirme un nettoyage. Sinon, USBGuard le bloque : sa
  règle est liée au numéro de série et au port du contrôleur tactile
  ([Périphériques USB](image.md#usb-devices)).
- Récupérez les journaux avec une [clé de maintenance](logs.md#maintenance-device) :
  `failed-services.txt` est vide, `signatures.txt` affiche le jeu installé.
