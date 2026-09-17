(cabinet front - tapered finger notch)
(drawing: mouth 65.0 over sharp corners, 71.651 over the R6.0 rounds)
(         floor 40.0, depth 20.0, flank 57.99 deg to the edge)
(machine: StepCraft M.1000 (WinPC-NC))
(datum:   X0 = notch centreline, Y0 = the panel edge it opens on,)
(         Z0 = TOP FACE of the panel, material into -Z)
(stock:   12.0 mm MDF on a sacrificial board)
(tool:    8.0 mm 2-flute, path is on the compensated centreline - no G41/G42)
(cut:     5 passes of 2.5, through to Z-12.5 (0.5 into the board))
(tabs:    2 x 8.0 wide, 1.5 high (top at Z-10.5), one per flank)
(feeds:   S17900 F2400, plunge F400)
(  feeds.py mdf d8 f2 ae8 ap2.500 -> S17905 F3000)
(  programmed at 0.8 of that: F2400 (0.067 mm/tooth))
(  0.55 kW at the cutter, 15 um tip deflection)
(  CLAMPED feed limited to F3000 by the machine (wanted 5730); real chip load falls to 0.0838 mm/tooth)
(  ! full slotting: chips have nowhere to go and the cutter is engaged 180 deg. Halve ap, or trochoid the slot instead)
(verify:  python3 -m gcode.cli <this file> -s <setup.json>)
(SET THE SPINDLE DIAL to the S value if WinPC-NC does not drive it)
G21 G17 G40 G90 G94
G54
M3 S17900
G0 Z5.0
G0 X-35.825 Y-6.0
(pass 1 of 5 - Z-2.5)
G0 Z1.0
G1 Z-2.5 F400
G1 X-35.825 Y-4.0 F2400
G3 X-27.346 Y0.7 I0.0 J10.0
G1 X-18.371 Y15.06
G2 X-16.675 Y16.0 I1.696 J-1.06
G1 X16.675 Y16.0
G2 X18.371 Y15.06 I0.0 J-2.0
G1 X27.346 Y0.7
G3 X35.825 Y-4.0 I8.48 J5.3
G1 X35.825 Y-6.0
G0 Z5.0
G0 X-35.825 Y-6.0
(pass 2 of 5 - Z-5.0)
G0 Z-1.5
G1 Z-5.0 F400
G1 X-35.825 Y-4.0 F2400
G3 X-27.346 Y0.7 I0.0 J10.0
G1 X-18.371 Y15.06
G2 X-16.675 Y16.0 I1.696 J-1.06
G1 X16.675 Y16.0
G2 X18.371 Y15.06 I0.0 J-2.0
G1 X27.346 Y0.7
G3 X35.825 Y-4.0 I8.48 J5.3
G1 X35.825 Y-6.0
G0 Z5.0
G0 X-35.825 Y-6.0
(pass 3 of 5 - Z-7.5)
G0 Z-4.0
G1 Z-7.5 F400
G1 X-35.825 Y-4.0 F2400
G3 X-27.346 Y0.7 I0.0 J10.0
G1 X-18.371 Y15.06
G2 X-16.675 Y16.0 I1.696 J-1.06
G1 X16.675 Y16.0
G2 X18.371 Y15.06 I0.0 J-2.0
G1 X27.346 Y0.7
G3 X35.825 Y-4.0 I8.48 J5.3
G1 X35.825 Y-6.0
G0 Z5.0
G0 X-35.825 Y-6.0
(pass 4 of 5 - Z-10.0)
G0 Z-6.5
G1 Z-10.0 F400
G1 X-35.825 Y-4.0 F2400
G3 X-27.346 Y0.7 I0.0 J10.0
G1 X-18.371 Y15.06
G2 X-16.675 Y16.0 I1.696 J-1.06
G1 X16.675 Y16.0
G2 X18.371 Y15.06 I0.0 J-2.0
G1 X27.346 Y0.7
G3 X35.825 Y-4.0 I8.48 J5.3
G1 X35.825 Y-6.0
G0 Z5.0
G0 X-35.825 Y-6.0
(pass 5 of 5 - Z-12.5)
G0 Z-9.0
G1 Z-12.5 F400
G1 X-35.825 Y-4.0 F2400
G3 X-27.346 Y0.7 I0.0 J10.0
G1 X-26.038 Y2.792 Z-12.5
G1 X-24.978 Y4.488 Z-10.5
G1 X-20.738 Y11.272 Z-10.5
G1 X-19.678 Y12.968 Z-12.5
G1 X-18.371 Y15.06
G2 X-16.675 Y16.0 I1.696 J-1.06
G1 X16.675 Y16.0
G2 X18.371 Y15.06 I0.0 J-2.0
G1 X19.678 Y12.968 Z-12.5
G1 X20.738 Y11.272 Z-10.5
G1 X24.978 Y4.488 Z-10.5
G1 X26.038 Y2.792 Z-12.5
G1 X27.346 Y0.7
G3 X35.825 Y-4.0 I8.48 J5.3
G1 X35.825 Y-6.0
G0 Z5.0
M5
M30
