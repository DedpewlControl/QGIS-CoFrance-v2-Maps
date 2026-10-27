# Generate a holding pattern in QGIS

Install **generate_holding.py** using **Processing Toolbox > Scripts > Add Script
to Toolbox**, then search for **Generate CoFrance holding pattern**.

The tool finds a named VOR, NDB, enroute FIX or procedure FIX and creates an editable line
GeoPackage using the CoFrance template. Processing adds the output to QGIS.

## Terminal and Enroute

Choose the **Holding type / drawing preset** at the top of the dialog:

| Type | Preset leg time | Preset drawing speed | Automatic FIX lookup |
|---|---|---|---|
| Terminal | 1 minute | 180 kt | FIX Procedures and FIX Enroute |
| Enroute | 1.5 minutes | 240 kt | FIX Enroute |

Both types also search VOR and NDB. These are editable cartographic presets,
not rules inferred from the hold type or altitude. Leave time/speed at **0**
to use the selected preset, or enter the published time and desired drawing
speed. Zero is resolved when running, so changing type never leaves the other
type's preset in the time/speed fields.

## Inputs

| Input | Meaning |
|---|---|
| Identifier | Exact fix/navaid code; case and surrounding spaces are ignored |
| Master source | Automatic uses the holding type's lookup; select VOR, NDB, FIX Enroute or FIX Procedures to override it |
| Master feature ID | Leave -1 unless an ambiguity message gives several matches |
| Inbound holding track | Direction flown **towards the fix**, clockwise from north |
| Track reference | True by default, or Magnetic |
| Magnetic variation | East positive, west negative; true = magnetic + variation |
| Holding turns | Right or Left, viewed along the inbound track |
| Straight-leg time | 0 uses the type preset; otherwise minutes for each leg, including 1.5 |
| Drawing speed | 0 uses the type preset; otherwise the drawing speed in knots |
| Minimum / maximum flight level | Optional lower/upper FL, for example 195 and 500; -1 means unspecified |
| Drawing turn rate | Degrees per second; default 3 |
| Holding GeoPackage | A new .gpkg file; existing outputs are refused |

At 180 kt, a one-minute straight leg is 3 NM. A turn rate of 3 degrees/second
gives a turn radius of approximately 0.955 NM. Increasing time lengthens the
straight legs; increasing speed lengthens the legs and widens the turns.

## Example

1. Select Terminal or Enroute, enter ABB and select LFXX_VOR.
2. Enter the **inbound** track and choose True or Magnetic.
3. For Magnetic, enter the chart's variation. This is not looked up automatically.
   Zero variation produces a warning and uses the entered track as true.
4. Choose Right or Left, leg time, drawing speed and optional minimum/maximum FL.
5. Choose a new output, such as Hold_ABB.gpkg inside your map's Procedures folder.
6. Run, then move the generated layer into the desired QGIS group.

If the chart gives an outbound course or a VOR radial, use its reciprocal for
the inbound track. Master feature ID and drawing turn rate are advanced inputs.

## CNA example

For the enroute hold parameters supplied in this conversation, enter:

| Setting | Value |
|---|---|
| Holding type | Enroute |
| Identifier / source | CNA / LFXX_VOR |
| Inbound track | 337 degrees |
| Straight-leg time | 1.5 minutes |
| Minimum FL | 195 |
| Maximum FL | 500 |

Set the turn direction and true/magnetic reference from the chart. Magnetic
tracks also require the chart's variation. Drawing speed can stay at 0 for the
Enroute preset of 240 kt, or be overridden. The flight levels are metadata;
they do not calculate TAS or change geometry.

## Result and lookup

The fix is the end of the inbound leg and start of the first turn. The closed
line follows the first turn, outbound leg, second turn and inbound leg back to
the fix. Right and left patterns mirror around the inbound axis.

The geometry is built in a local azimuthal equidistant projection centred on the
fix and saved in WGS84. The internal layer name matches the output filename.
The saved grey line style, CoFrance name and activation fields can be edited and
exported with the existing plugin. Additional fields retain all holding inputs,
the source master/feature ID, leg length, turn radius, hold type and minimum/
maximum flight levels. Fields min_fl and max_fl are integers or null; level_text
stores a readable range such as FL195–FL500. For QGIS labels, choose hold_label,
which includes the fix, track reference, turn direction, time and flight levels.

The current exporter exports the holding line; it does not export these extra
holding fields as altitude-based visibility rules or render QGIS line labels as
separate CoFrance text. Use a CoFrance text layer if exported annotation is
needed.

Loaded LFXX_VOR, LFXX_NDB, LFXX_FIX_ENR and LFXX_FIX_PROC layers take precedence over files in
QGIS/navdata. Source filenames also identify masters. If an identifier matches
more than one point, the error lists source, feature ID and coordinates. Choose
the source and ID for the intended point. Terminal lookup accepts a FIX present
in both Enroute and Procedures within 1 metre as the same anchor, preferring the
Procedure copy and logging that choice. Distant matches remain ambiguous.

The script runs independently of the other custom scripts. QGIS_DIRECTORY at
the top fixes the repository path, as in the existing CoFrance tools; change it
if the repository moves. Source features and existing outputs are preserved.

## Drawing model

This is a cartographic racetrack with equal straight legs and circular turns in
still air. Wind, altitude, bank-angle limits and protected airspace are not
modelled. It does not infer a published hold from the navigation database.
The default turn rate is a drawing setting, not a flight-performance guarantee.

## Checks

Run test_generate_holding.py with QGIS Python. Checks cover turn direction,
inbound orientation, lengths, magnetic conversion, projected masters, duplicate
identifiers, terminal/enroute presets, procedure FIX lookup, flight levels,
template fields, plugin export, output protection and cancellation.

