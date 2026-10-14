# Enclosure of the kiosk

A 3D-printed desk enclosure for the reference hardware
([docs/hardware.md](../docs/hardware.md)): a Lenovo ThinkCentre Tiny and a
screen, in two versions.

| File | Content |
|---|---|
| `pandora-box-7inch.stl` | the model for a 7-inch screen such as the Waveshare 7-inch HDMI LCD (C) |
| `pandora-box-7inch.png` | views of the 7-inch model (front, back, right, bottom) |
| `pandora-box-7inch-v2.stl` | the 7-inch model, 1.5 mm wider on the right for angled HDMI and USB cables |
| `pandora-box-7inch-v2.png` | views of the 7-inch v2 model (front, back, right, bottom) |
| `pandora-box-10inch.stl` | the model for a 10-inch screen |
| `pandora-box-10inch.png` | views of the 10-inch model (front, back, top, bottom) |
| `LICENSE` | CERN Open Hardware Licence Version 2 - Strongly Reciprocal |

Each model is one part, about 191 x 184 x 86 mm (binary STL, millimetres).
The front face, tilted, holds the screen in its window; the rounded back has
ventilation slots on its top.

![Views of the 7-inch enclosure](pandora-box-7inch.png)

The 10-inch model has a 180 x 128 mm window. The 7-inch model is sized for
a 165 x 100 mm screen: a 14 mm band at the top and at the bottom of the
window leaves a 100 mm high opening. Each band has two 3 mm holes to screw
the screen from behind, 157.5 mm apart across the face and 115 mm apart
along the slope. Its side walls are 2 mm thick instead of 5 mm behind the
screen, to leave room for the HDMI and USB cables. On each side of the
window, a 12 mm wide and 5 mm thick edge, flush with the front face, joins
the top and bottom bands.

The 7-inch v2 model is for angled HDMI and USB cables, which stick out
1.5 mm past the right edge of the screen: its right side is moved 1.5 mm
outward, so the model is 185.5 mm wide. The window, the screen holes and
the base holes do not move, and the side walls keep their 2 mm thickness
behind the screen; the right base hole is now 10 mm from the side edge.

The base of both models has three 3.8 mm holes to screw it on the VESA
mounting bracket of the Lenovo ThinkCentre Tiny (M720), 8.5 mm from the side
edges: two on the left, 27.15 and 157.15 mm from the front edge, and one on
the right, 92.15 mm from the front edge.

![Views of the 10-inch enclosure](pandora-box-10inch.png)

## Licence

Copyright the PandoraBox contributors.

This source describes Open Hardware and is licensed under the CERN-OHL-S
v2 (`LICENSE`, https://ohwr.org/cern_ohl_s_v2.txt).

You may redistribute and modify this source and make products using it
under the terms of the CERN-OHL-S v2. This source is distributed WITHOUT ANY
EXPRESS OR IMPLIED WARRANTY, INCLUDING OF MERCHANTABILITY, SATISFACTORY
QUALITY AND FITNESS FOR A PARTICULAR PURPOSE. Please see the CERN-OHL-S v2
for applicable conditions.

Source location: https://github.com/dbarzin/pandora-box/tree/main/3D

As per CERN-OHL-S v2 section 4, should you produce hardware based on this
source, you must where practicable maintain the Source Location visible on
the external case of the enclosure or other products you make using this
source.
