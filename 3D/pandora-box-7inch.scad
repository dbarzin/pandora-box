// PandoraBox - enclosure for the kiosk, 7-inch screen version
//
// Copyright the PandoraBox contributors.
// Licensed under the CERN-OHL-S v2 (see LICENSE).
// Source location: https://github.com/sourcentis/pandora-box/tree/main/3D
//
// Units are millimetres. X goes from the back to the front, Y across the
// face, Z up. The front face is tilted and holds the screen in its window;
// the back is rounded, with ventilation slots on its top.
//
// Render with:  openscad -o pandora-box-7inch.stl pandora-box-7inch.scad

// --- Screen position ---------------------------------------------------

// Distance the screen moves up along the front face, from the original
// model. The front face gets longer by the same amount at the bottom:
// the top and the back of the enclosure move with the screen, so the
// enclosure gets deeper and taller, while the front edge and the base
// holes stay in place.
screen_shift = 10;

// --- Dimensions ----------------------------------------------------------

width        = 184;     // across the face
side_wall    = 2;       // side walls
floor_t      = 3;       // base thickness
face_t       = 2;       // front face thickness
face_out     = 10;      // outer side of the front face, out of the slope
                        // (set by the rounded back)
face_angle   = 36.2021; // tilt of the front face from the vertical
front_x      = 100;     // front edge of the base
face_length  = 131.244; // length of the original front face, along the slope

back_r_out   = 85.586;  // rounded back, outer radius
back_r_in    = 81.868;  // rounded back, inner radius
back_x_out   = -85;     // back wall, outside
back_x_in    = -81.275; // back wall, inside

// Screen recess behind the bottom of the front face
recess_start = 1.744;   // from the bottom edge, along the slope

// Window and screen holes (positions along the slope are measured from the
// bottom edge of the front face, before the shift)
window_w     = 156;
window_h     = 93;
window_ext_l = 3;       // window edges moved outwards, seen from the front:
window_ext_r = -3;      // left and right (negative = inwards)
window_u     = 22.744;
hole_d       = 3;
hole_csk_d   = 5.2;     // 90° countersink on the outer face (M2.5 flat head)
hole_y       = 78.75;   // half the distance across the face (157.5 mm)
hole_u       = [8.244, 123.244]; // 115 mm apart along the slope
hole_dy      = -3;      // offset across the face (negative = to the left,
                        // seen from the front)

// Base
base_lip     = 10;      // floor left along the walls, inwards
base_boss    = 8;       // floor left around the base holes
base_hole_d  = 3.8;
base_csk_d   = 7;       // 90° countersink on the inner side (M3 flat head)
base_holes   = [[-57.15, -83.5], [72.85, -83.5], [7.85, 83.5]];

// Access hole in the top, above a base hole, to reach its screw
access_d     = 5;
access_hole  = base_holes[0];   // back left, seen from the front

// Cable opening at the back
cable_y      = [-13, 37];
cable_z      = [9.838, 26.838];

// Ventilation slots on top
vent_x       = [-53.077, -30.077];
vent_w       = 2;
vent_y       = [-30, -20, -10, 0, 10, 20, 30];

arc_steps    = 60;

// --- Derived values ------------------------------------------------------

face_in = face_out - face_t;               // inner side of the front face
U = [-cos(face_angle), sin(face_angle)];  // up the slope, in the XZ plane
N = [ sin(face_angle), cos(face_angle)];  // out of the front face
F = [front_x, 0];                          // front edge of the base

// Point on the profile from coordinates along the slope (u) and out of
// the face (n), measured from the front edge of the base.
function face_pt(u, n) = F + u * U + n * N;

// The rounded back is centred on C; it moves with the screen.
C = screen_shift * U;
L = face_length + screen_shift;

// Opening in the base: the floor is left base_lip wide along the walls,
// measured on its top from the back wall and from the foot of the front face.
base_open_x = [C[0] + back_x_in + base_lip,
               face_pt(floor_t / sin(face_angle), 0)[0] - base_lip];
base_open_w = width - 2 * side_wall - 2 * base_lip;

function arc(r, a0, a1) =
    [for (i = [0:arc_steps]) C + r * [cos(a0 + (a1 - a0) * i / arc_steps),
                                      sin(a0 + (a1 - a0) * i / arc_steps)]];

// Inner top corner: where the inner face meets the inner rounded back.
function inner_apex_u() =
    let(d = face_pt(0, face_in) - C, b = d * U, c = d * d - back_r_in * back_r_in)
    -b + sqrt(b * b - c);

// --- Profiles (XZ plane) -------------------------------------------------

function outer_profile() =
    let(a_back = acos(back_x_out / back_r_out))
    concat([[C[0] + back_x_out, 0], F, face_pt(0, face_out)],
           arc(back_r_out, 90, a_back));

function inner_profile() =
    let(a_apex = atan2(face_pt(inner_apex_u(), face_in)[1] - C[1],
                       face_pt(inner_apex_u(), face_in)[0] - C[0]),
        a_back = acos(back_x_in / back_r_in))
    concat([[C[0] + back_x_in, floor_t],
            face_pt(floor_t / sin(face_angle), 0),
            face_pt(recess_start, 0),
            face_pt(recess_start, face_in)],
           arc(back_r_in, a_apex, a_back));

// --- Parts ---------------------------------------------------------------

module extrude_profile(h) {
    rotate([90, 0, 0]) linear_extrude(height = h, center = true) children();
}

// Place children in the frame of the front face: X along the slope (from
// the bottom edge), Y across, Z out of the face.
module on_face(u) {
    translate([F[0], 0, F[1]])
        rotate([0, face_angle, 0]) translate([-u, 0, 0]) children();
}

module window() {
    on_face(window_u + screen_shift + window_h / 2)
        translate([0, (window_ext_r - window_ext_l) / 2, (face_in + face_out) / 2])
            cube([window_h, window_w + window_ext_l + window_ext_r, face_t + 2],
                 center = true);
}

module screen_holes() {
    for (u = hole_u, y = [-hole_y, hole_y])
        on_face(u + screen_shift) translate([0, y + hole_dy, 0]) {
            translate([0, 0, face_in - 1])
                cylinder(d = hole_d, h = face_t + 2, $fn = 48);
            translate([0, 0, face_out - (hole_csk_d - hole_d) / 2])
                cylinder(d1 = hole_d, d2 = hole_csk_d + 2,
                         h = (hole_csk_d - hole_d) / 2 + 1, $fn = 48);
        }
}

module base_cuts() {
    difference() {
        translate([base_open_x[0], -base_open_w / 2, -floor_t / 2])
            cube([base_open_x[1] - base_open_x[0], base_open_w, 2 * floor_t]);
        for (p = base_holes)
            translate([p[0], p[1], floor_t / 2])
                cylinder(r = base_hole_d / 2 + base_boss, h = 2 * floor_t + 2,
                         center = true, $fn = 64);
    }
    for (p = base_holes)
        translate([p[0], p[1], 0]) {
            translate([0, 0, -1])
                cylinder(d = base_hole_d, h = floor_t + 2, $fn = 32);
            translate([0, 0, floor_t - (base_csk_d - base_hole_d) / 2])
                cylinder(d1 = base_hole_d, d2 = base_csk_d + 2,
                         h = (base_csk_d - base_hole_d) / 2 + 1, $fn = 32);
        }
}

module cable_opening() {
    translate([C[0] + back_x_out - 5, cable_y[0], C[1] + cable_z[0]])
        cube([15, cable_y[1] - cable_y[0], cable_z[1] - cable_z[0]]);
}

module access_hole() {
    translate([access_hole[0], access_hole[1], floor_t + 1])
        cylinder(d = access_d, h = 200, $fn = 32);
}

module vents() {
    for (y = vent_y)
        translate([C[0] + vent_x[0], y - vent_w / 2, C[1] + 50])
            cube([vent_x[1] - vent_x[0], vent_w, 50]);
}

module enclosure() {
    difference() {
        extrude_profile(width) polygon(outer_profile());
        extrude_profile(width - 2 * side_wall) polygon(inner_profile());
        window();
        screen_holes();
        base_cuts();
        cable_opening();
        access_hole();
        vents();
    }
}

enclosure();
