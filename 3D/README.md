# Enclosure of the kiosk

A 3D-printed desk enclosure for the reference hardware
([docs/hardware.md](../docs/hardware.md)): a Lenovo ThinkCentre Tiny and a
screen, in two versions.

| File | Content |
|---|---|
| `pandora-box-7inch.stl` | the model for a 7-inch screen such as the Waveshare 7-inch HDMI LCD (C) |
| `pandora-box-7inch.scad` | the OpenSCAD source of the 7-inch model |
| `pandora-box-7inch.png` | views of the 7-inch model (front, back, right, bottom) |
| `pandora-box-10inch.stl` | the model for a 10-inch screen |
| `pandora-box-10inch.png` | views of the 10-inch model (front, back, top, bottom) |
| `LICENSE` | CERN Open Hardware Licence Version 2 - Strongly Reciprocal |

Each model is one part (binary STL, millimetres): the 10-inch model is
about 191 x 184 x 86 mm, the 7-inch model about 199 x 184 x 92 mm. The front
face, tilted, holds the screen in its window; the rounded back has
ventilation slots on its top.

![Views of the 7-inch enclosure](pandora-box-7inch.png)

The 10-inch model has a 180 x 128 mm window. The 7-inch model is sized for
a 165 x 100 mm screen: a 156 x 93 mm window, 3 mm left of centre seen from
the front, with a 15.5 mm band above it and a 32.7 mm band below it. Each
band has two 3 mm holes to screw the screen from the front with M2.5
flat-head screws (90° countersink, 5.2 mm across), 157.5 mm apart
across the face, 3 mm left of centre, and 115 mm apart along the slope. Its side walls are 2 mm thick, to leave room for the HDMI and
USB cables. On each side of the window, a 2 mm thick edge (9 mm wide on the left,
15 mm on the right),
flush with the front face, joins the top and bottom bands.

The 7-inch model is generated from `pandora-box-7inch.scad`:

```sh
openscad -o pandora-box-7inch.stl pandora-box-7inch.scad
```

OpenSCAD writes an ASCII STL. Its `screen_shift` parameter moves the screen
up the front face: it is 10 mm, so that the screen mounts have about 15 mm
of room behind the bottom holes, above the base. The front face is longer
by the same amount at the bottom, and the top and back of the enclosure
move with the screen; the front edge and the base holes do not move.

The base of both models has three 3.8 mm holes to screw it on the VESA
mounting bracket of the Lenovo ThinkCentre Tiny (M720), 8.5 mm from the side
edges: two on the left, 27.15 and 157.15 mm from the front edge, and one on
the right, 92.15 mm from the front edge. On the 7-inch model, they are countersunk
(90°, 7 mm across) on the inside, for M3 flat-head screws.

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

Source location: https://github.com/sourcentis/pandora-box/tree/main/3D

As per CERN-OHL-S v2 section 4, should you produce hardware based on this
source, you must where practicable maintain the Source Location visible on
the external case of the enclosure or other products you make using this
source.
