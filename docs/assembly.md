# Kiosk assembly

This page describes the assembly of a kiosk: the touchscreen in the
3D-printed enclosure, the enclosure on the support of the computer, then the
computer in its support. The validated hardware is described in
[Hardware](hardware.md).

![3D-printed enclosure, front view](images/b1.jpg){ width="400" }

## Parts { #parts }

| Part | Quantity | Note |
|---|---|---|
| 3D-printed enclosure | 1 | Model in [3D/](https://github.com/sourcentis/pandora-box/tree/main/3D) (see [Enclosure](hardware.md#enclosure)) |
| Waveshare 7 inch HDMI LCD (C) touchscreen | 1 | |
| Câble plat HDMI FPV mâle vers le haut 90D HDMI mâle HDTV FPC | 1 | Image of the screen |
| Câble plat micro USB 2.0 mâle vers micro USB mâle coudé à 90 degrés vers le haut | 1 | Touch controller and power of the screen |
| Lenovo ThinkCentre M720q Tiny | 1 | |
| Lenovo ThinkCentre Tiny VESA Mount II (4XF0N03161) | 1 | Metal support of the ThinkCentre, the enclosure is screwed onto its VESA holes |
| M3x8 Phillips head screws | 7 | 4 for the screen, 3 for the support |
| Phillips screwdriver | 1 | Long and thin, to reach the screws at the back of the enclosure |

The angled cables fit in the little space between the screen and the back of
the enclosure.

## 1. Insert the screen { #screen }

Before starting, turn on the backlight switch on the back of the screen: it
is no longer reachable once the kiosk is assembled.

Insert the screen into the enclosure through the bottom opening.

![Insertion of the screen through the bottom opening](images/b2.jpg){ width="400" }

## 2. Connect the cables { #cables }

Plug the angled HDMI and USB cables into the screen, inside the enclosure,
and lead them out through the opening at the back of the enclosure.

![Cables plugged into the screen, inside the enclosure](images/b3.jpg){ width="400" }

## 3. Screw the enclosure onto the support { #enclosure }

Put the enclosure on the Tiny VESA Mount II and fix it with 3 screws. The fourth screw, inside, is not reachable and stays empty.

| | |
|---|---|
| ![Screwing the enclosure, left side](images/b4.jpg) | ![Screwing the enclosure, right side](images/b5.jpg) |

## 4. Screw the screen { #screen-screws }

Fix the screen to the enclosure with the 4 remaining screws, one per corner.
This is tricky: start with one corner without tightening, then place the
others, tilting the enclosure on its side or forward if needed to line up
the holes. Tighten the 4 screws once they are all in place.

![Screwing the screen, enclosure tilted](images/b6.jpg){ width="400" }

## 5. Install the ThinkCentre { #computer }

Slide the ThinkCentre into the Tiny VESA Mount II, front face visible, then plug the
HDMI and USB cables into the back of the computer.

![Assembled kiosk](images/b7.jpg){ width="400" }

The kiosk is ready for the [initial installation](install.md).
