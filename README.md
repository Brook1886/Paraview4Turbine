# VECLYS Postprocessor

ParaView 6.1 Python plugins for turbomachinery CFD, and a toolbar button for
each filter. The reader turns a CGNS file into one Pipeline Browser entry whose
volume zones and named boundaries are checkboxes, so opening ten files gives ten
entries rather than a hundred, and a second reader opens a VECLYS run by the
`input.au3x` that describes it rather than by hunting for the mesh it wrote. The filters on top of it repeat a passage round
the annulus -- or along the cascade, and at the pitch the case file gives rather
than one anybody typed -- work out the quantities the solver did not write,
profile a slice from hub to casing, and integrate one down to a row of numbers.
Nothing here changes or rebuilds ParaView.

| Reader | Opens | What it gives |
| --- | --- | --- |
| CGNS (labeled boundaries) | `*.cgns` | volumes and named boundaries as checkboxes |
| VECLYS case | `input.au3x` | the same, for the case a run was set up with |

| Button | Filter | What it is for |
| --- | --- | --- |
| Circumferential Copy | `Circumferential Copy` or `Linear Copy` | one passage becomes a sector or a full wheel, at the case's own pitch |
| Flow Properties | `Flow Properties` | Q-criterion, lambda2, vorticity, schlieren |
| Radial Profile | `Radial Profile` | a slice becomes a hub-to-casing plot |
| Surface Average | `Surface Average` | a slice becomes one row: area, mass flow, averages |
| Plot CSV Files | — | several csv files drawn together on one chart |

There is a manual in `doc/` as well — the same ground as this README, laid out
to be read or printed rather than scrolled, with a worked picture of a stage
closed into a wheel. `doc/veclys-postprocessor.pdf` is kept in the
repository so reading it needs no TeX; after editing the `.tex` beside it,
rebuild with `make -C doc`.

## Start it

```bash
./paraview_diy.sh
```

The launcher does four things: it sets `PV_PLUGIN_PATH` to `plugins/`, which
ParaView scans and auto-loads; it puts `pylib/` on `PYTHONPATH` for the macros,
which the plugins also do for themselves; it links every file in `macros/` into
`~/.config/ParaView/Macros`, which is what puts the buttons on the **Macros**
toolbar; and it runs `auto_color_cgns.py` as a startup script to pick the first
coloring. The links point back at this repository, so editing a macro here is
enough, and a file of the same name that is not a link is left alone.

### On somebody else's machine

Nothing has to be edited. The launcher takes the checkout to be whatever
directory it is sitting in, and finds ParaView through `PARAVIEW_ROOT`, `PATH`,
or common user and system installation directories. An unpacked distribution
can be selected without editing the launcher:

```bash
git clone <this repo> ~/paraview_diy
export PARAVIEW_ROOT=/path/to/ParaView
~/paraview_diy/paraview_diy.sh
```

There is nothing to install alongside it. ParaView 6.1.1 already carries
everything the plugins import — h5py 3.9 for the CGNS section metadata, numpy,
and the scipy the verification suites use — so no `pip` step and no build.
`zenity` is wanted by the two buttons that ask for files, **Plot CSV Files** and
its picker; without it they say so and fall back to the source already selected.

Two things do not travel with the repository. The CGNS meshes are gitignored, so
`verify_plugin.py` and `verify_circumferential_copy.py` want a file of your own
as their argument rather than the `bfs.cgns` and `r37.cgns` they default to; and
the case checks read reference runs supplied by the user. Pass one or more
`input.au3x` files to `verify_veclys_reader.py`, or set `PARAVIEW_DIY_CASES_DIR`
to a folder containing case subfolders (the default is `TestCase/veclys` inside
the checkout). `verify_passage_copies.py` looks for `stage`, `wheel`, `cascade`,
and `duct` subfolders there and reports missing cases. Override individual case
files with `PARAVIEW_DIY_STAGE_CASE`, `PARAVIEW_DIY_WHEEL_CASE`,
`PARAVIEW_DIY_CASCADE_CASE`, and `PARAVIEW_DIY_DUCT_CASE`.
`verify_auto_color.py` accepts an optional VECLYS case through
`PARAVIEW_DIY_COLOR_CASE`. These settings are local; test data is not included.

### Starting ParaView some other way

`PV_PLUGIN_PATH` is read at startup, so a session begun from a desktop icon or a
plain `paraview` has none of the plugins, and the Open dialog has never heard of
`input.au3x`. ParaView's own answer is the **Auto Load** checkbox in **Tools >
Manage Plugins**, which it remembers in its settings file. To tick them all at
once, with ParaView closed:

```bash
./install_autoload.py
```

```
Macros:
  Circumferential Copy     is now a button
  ...
Plugins:
  cgns_labeled_boundaries  now loads itself
  circumferential_copy     now loads itself
  flow_properties          now loads itself
  linear_copy              now loads itself
  slice_averages           now loads itself
  veclys_reader            now loads itself
```

It links the macros whatever else is going on, since that is only a directory of
symlinks, and keeps a copy of the settings as
`ParaView.ini.before-veclys-postprocessor` the first time. The plugin half refuses
to run while ParaView is open, because ParaView writes the whole settings file
out when it closes and would undo the change.

### Toolbar icons

`install_autoload.py` writes to `~/.config/ParaView/ParaView.ini`, which is where
ParaView 6.1 keeps its settings; `--settings` points it somewhere else if your
build does not.

The macro buttons can carry a logo apiece rather than their names. The logos are
in `icons/`, and `install_icons.py` writes them into the settings so the Macros
toolbar shows them icon-only:

```bash
./install_icons.py            # ParaView closed, 6.1 settings
```

It maps each macro to `icons/<macro>.svg` and keeps a backup of the settings. To
set them by hand instead, use **Macros > Edit Macros** and browse to the svgs.

### The buttons

The buttons work either way. ParaView runs a macro as a bare string, with no
`__file__` of its own to find `pylib/` by, so each plugin puts `pylib/` on the
path as it loads — a plugin does know where it lives. Press a button with no
plugin loaded at all and it says so rather than throwing. Plugins can also be
loaded one at a time from **Manage Plugins > Load New**.

## Use it

1. Open a `.cgns` file. ParaView asks which reader to use, because the stock
   CGNS reader also claims the extension: choose **CGNS (labeled boundaries)**.
2. Tick the volumes and boundaries you want in the Properties panel.
3. Click **Apply**.

The Properties panel offers:

- **Volumes** — one checkbox per volume-bearing CGNS zone;
- **Boundaries** — one checkbox per named boundary-face `Elements_t` section;
- **CellArrays** / **PointArrays** — the CGNS solution arrays, as usual;
- **Cell Data to Point Data** — on by default, see below.

Unticking a boundary drops it from the output, and unticking everything in a
base means that base is never read at all.

## Opening a VECLYS case

A run is a directory with an `input.au3x` in it, and the meshes are buried a few
folders down under names that file only implies. **VECLYS Reader** takes the
input file and works the rest out, so opening a case is opening the file that
describes it:

```
File > Open > /path/to/cases/stage/input.au3x
```

`[devicelist] device` is xcode's device tree: comma-separated chains, hyphens
between the levels of a chain, and each level written `type:name`. Every device
gets a folder of its own name, every device gets a folder for the case number
inside that, and the leaf writes its mesh there under its own name. So a single
blade row,

```ini
device = empty:rotor37-dom:r37
```

with case `0000` means `rotor37/r37/0000/r37.cgns`, and a stage of three rows,

```ini
device = empty:e3-dom:IGV,empty:e3-dom:R1,empty:e3-dom:S1
```

means three meshes, `e3/IGV/0000/IGV.cgns` and its fellows, **all of which are
read**. Each row is a block of the output under its own name:

```
Root
├── IGV
│   ├── Volumes
│   │   └── IGV
│   └── Boundaries
│       ├── B0_quad_BLADE
│       └── …
├── R1
└── S1
```

The panel is the CGNS reader's, over all the rows at once:

- **Case Number** — `0000`, which is the only case any run has written so far.
  Point it at one that was never run and it names the mesh it looked for and
  hands back nothing, rather than failing;
- **Reload Case** — look for the case again. A case opened before its run had
  written the mesh is not settled for good: nothing in the panel has changed, so
  ParaView has no reason to ask the reader anything, and this is what asks it.
  Ticking anything else does as well;
- **Domains** — one checkbox per blade row. Untick one and its file is not read
  at all, which is worth having when a stage is three meshes of 50 MB;
- **Volumes**, **Boundaries**, **CellArrays**, **PointArrays** — as ever, over
  every row that is ticked. A name carries its row's name only when more than
  one row uses it, the way the CGNS reader only qualifies a section with its
  zone when the section name is not unique. Every row has a `B5_quad_HUB`, so
  those are listed as `IGV/B5_quad_HUB` and so on, while `IGV`, `R1` and `S1`
  are volumes enough on their own. The count is over the whole case, not over
  what is ticked, so unticking a row leaves the names of the others — and the
  ticks against them — where they were.

The settings each row was run with are in `input.au3x` itself: one `[<row>]`
section apiece, holding the shaft speed (`frame-speed`), the turbulence model
and the rest. Three of them say how the passage repeats — the coordinate system
(`cosystem`) and, by it, the blade count (`assembly-sectors`) or the cascade
pitch (`pitch`) — so the **Circumferential Copy** button reads those off the
case rather than asking; see
[Circumferential copies](#circumferential-copies) below.

An earlier version copied the settings onto the output as `veclys:` field data
so they showed in the Information panel, but ParaView lists every field-data
array in the Coloring dropdown as well, where a row's worth of `veclys:` names
bury the flow quantities — so they are read from the file they came from, by
`pylib/veclys_case.py`, which is the reader's own parser and the button's.

## Showing and coloring individual boundaries

The output is one multiblock:

```
Root
├── Volumes
│   └── r37
└── Boundaries
    ├── BLADE_quad
    ├── RIGHT_quad
    └── …
```

Use the **Multi-block Inspector** (View > Multi-block Inspector) to show, hide,
color and set the opacity of each boundary independently — that panel is
ParaView's equivalent of a per-boundary eye icon, and it keeps working no matter
how many files are open. From Python the same control is the representation's
`BlockSelectors`:

```python
GetDisplayProperties(reader).BlockSelectors = ['/Root/Boundaries/BLADE_quad']
```

## Automatic Cell Data to Point Data

CGNS stores these solutions at cell centers, which colors as flat facets. Each
block therefore runs **Cell Data to Point Data** on its own cells.

Every array appears **once**. `PassCellData` is off, so ticking the box moves the
arrays onto the points rather than duplicating them, and the coloring dropdown
lists each solution array a single time instead of offering a POINT_DATA and a
CELL_DATA copy of the same numbers. Untick the box to keep the raw cell arrays
instead; then nothing is offered as point data.

From Python the checkbox is spelled `CellDatatoPointData`, because ParaView
builds the attribute name from the label:

```python
reader.CellDatatoPointData = 0     # keep the cell arrays instead
```

Because each block converts only its own cells, values are never averaged across
two different boundaries or zones.

## Automatic coloring

ParaView paints every new source Solid Color, so the interpolated point arrays
would sit there unused until someone opened the Coloring dropdown.
`auto_color_cgns.py` makes that first choice instead: on the render that follows
Apply, each source from either of this project's readers is colored by a point
array and given a scalar bar.

- `Pressure` is preferred, then `Temperature`, `Density`, `Mach`, then whatever
  point array comes first. Set `PARAVIEW_DIY_COLOR_BY` to lead with another
  name, for example `PARAVIEW_DIY_COLOR_BY=Temperature`.
- It acts once per source. Change the coloring afterwards and it is never
  touched again.
- Nothing else in the pipeline is touched, only these two readers.
- Opening a second file widens the shared color map to cover both files rather
  than replacing its range, so the first file does not go flat.

It also switches off ParaView's **Color by block colors on apply** setting.
Otherwise every multiblock source comes up colored by its block index and leaves
a `vtkBlockColors` color bar parked in the render view even after the coloring
moves to a real array; any bar Apply already created is hidden as well.

Every boundary block also carries:

- `BoundaryId`, for coloring or thresholding by boundary. It is constant across a
  boundary, so it is interpolated onto the points exactly along with everything
  else;
- `CGNSElementId`, the original 1-based CGNS element ID. An element id has no
  meaningful value at a point, so it is only present when **Cell Data to Point
  Data** is unticked;
- `CGNSZoneName` field data, naming the zone it was cut from, either way.

## Circumferential copies

A turbomachinery mesh holds one blade passage, so one passage is what the file
draws. The **Circumferential Copy** filter rotates copies of its input about the
machine axis and returns them as one multiblock. Press the **Circumferential
Copy** button on the Macros toolbar with a source selected — that is the whole
of the toolbar part — or pick the filter out of **Filters > Alphabetical**. If
the toolbar is not showing, **View > Toolbars > Macros** brings it back.

The button creates the filters and leaves them unapplied, the way picking a
filter from the menu does, so a 500,000-cell volume is not repeated thirty-six
times before the numbers have been checked. It fills in three things first:

- the **pitch**, out of `input.au3x` when the mesh came from a VECLYS case —
  which is the whole of it, since the file says everything there is to say and
  nothing is left to guess. A case of several blade rows fills in a line per row
  and stays one filter. See [A row at a time, from the case
  file](#a-row-at-a-time-from-the-case-file) below;
- the **machine axis**, guessed from a sample of a few thousand of the input's
  points: a passage never crosses its own axis and covers a narrow wedge about
  it, so the axis it stays clear of and looks narrowest from is the one to pick.
  The guess and the wedge it measured go to the **Output Messages** window, as a
  warning when the answer is not clear-cut, which is also how the button reports
  having nothing to do — no source selected, or the plugin not loaded. A single
  blade surface is genuinely ambiguous; a whole passage is not;
- the settings of the **last copy in the pipeline**, so anything typed by hand
  is typed once rather than once per boundary. What the case file settles is
  then set from the case, which is the more recent word on it.

The Properties panel offers:

- **Rotation Axis** — X, Y, Z, or an **Axis Direction** typed in;
- **Axis Origin** — a point on the axis, which is 0 0 0 for the usual mesh;
- **Full Annulus** — repeat every block its own blade count of times, whatever
  the table's copies column says. This is the usual answer and the one number
  nobody should have to work out: rows of 32, 28 and 50 blades close the wheel
  together, at 32, 28 and 50 copies. It belongs to this filter alone — a
  cartesian case gets a [Linear Copy](#linear-copies), which has no wheel to
  close and so no such tick;
- **Blade Rows** — the table, and everything else the filter knows. A line per
  row: its **name**, its **blade count** and how many **copies** of it to make,
  counting the original. A block the table names turns by 360 / its own blade
  count, that many times, and hands both down to everything under it.

  The **line with no name** is what every other block goes by, which is all a
  mesh with no blade rows in it needs — a plain CGNS file, a sphere. The filter
  starts with one such line, 36 blades and 4 copies. A cell left empty keeps
  whatever that line says, so a line can set the copies alone, and a **negative
  blade count** copies the other way round: −48 blades is a pitch of −7.5°.
  Anything the table cannot mean is refused by the line it is on — two lines for
  one row, a cell that is not a number, half a blade — and nothing is drawn
  rather than something wrong;
- **Rotate Vectors** — on by default, see below.

A line naming something the input has no block of, and a block the table leaves
with no pitch at all, are both reported by name in the **Output Messages**
window; the block is copied once, where it is, rather than failing the filter.

From Python:

```python
copies = CircumferentialCopy(Input=reader)
copies.RotationAxis = 'X'
copies.BladeRows = ['', '36', '4']   # no name: everything the input holds
copies.FullAnnulus = 1               # 36 copies, the whole wheel
```

or, for a stage of three rows in one filter — the table is flat, three strings
to a line:

```python
copies = CircumferentialCopy(Input=case)
copies.RotationAxis = 'X'
copies.BladeRows = ['IGV', '32', '5',
                    'R1',  '28', '4',
                    'S1',  '50', '7']
```

### A row at a time, from the case file

A blade count is a thing to look up, and looking it up is the part the case file
makes unnecessary: a run's `input.au3x` was written with the answer in it. Press
the button on anything built on a **VECLYS Reader** and two keys of each
`[<row>]` section are read:

- **`cosystem`** says whether there is an axis at all. `annular` is a machine
  with one; `cartesian` is a straight cascade, which has none, and the button
  makes a [Linear Copy](#linear-copies) for it instead. This is read *first*,
  because a cartesian section keeps an `assembly-sectors` that means nothing —
  `half_cylinder` is a straight duct carrying `assembly-sectors=12` — and taking
  that at face value would invent a 30° pitch for a mesh with no axis in it;
- **`assembly-sectors`** is the blade count of an annular row, and **`pitch`**
  the passage spacing of a cartesian one.

**A stage is one filter.** The rows have their own blade counts, so they have
their own pitches and their own numbers of copies — and both are a line of the
**Blade Rows** table rather than a filter of their own in the pipeline:

| | name | blades | copies |
| --- | --- | --- | --- |
| 1 | `IGV` | `32` | `5` |
| 2 | `R1` | `28` | `4` |
| 3 | `S1` | `50` | `7` |

The button fills it in, names and all, so nothing there has to be typed — the
last column is the only one worth touching. A block the table names hands its
pitch and its count down to everything under it, so the three lines reach every
boundary of every row.

The copies start at four of the widest row's pitch — the same four the filter
has always started with — and each row gets as many as its own pitch needs to
reach the same place, so the stage lines up and pressing **Apply** does not
build a whole wheel by accident. For the whole wheel, tick **Full Annulus**: one
tick closes it for all three at once, at 32, 28 and 50 copies respectively,
which is exactly what comes out:

```
Circumferential copy: /path/to/cases/stage/input.au3x
  IGV: annular, 32 blades, pitch 11.25 deg
  R1: annular, 28 blades, pitch 12.8571 deg
  S1: annular, 50 blades, pitch 7.2 deg
Circumferential copy: the machine axis looks like X (the mesh covers 12 deg
  about X, 52 deg about Z, 214 deg about Y).
Circumferential copy: 3 rows in one filter, a line each -- IGV turns 11.25 deg,
  5 times; R1 turns 12.8571 deg, 4 times; S1 turns 7.2 deg, 7 times -- so each
  covers the same 51.43 degrees. Tick Full Annulus for the whole wheel.
```

A line naming something the input does not have is reported by name in the
**Output Messages** window rather than silently doing nothing, which is the
other half of not having to be careful with the names.

Which rows the selected source carries is decided by the block names, so
extracting `R1` yourself and pressing the button gives a table of one line,
`R1`'s, and nothing else.

Opened as a plain CGNS file there is no case to read and nothing changes: the
axis is guessed, the settings of the last copy are inherited, and the blade
count is typed into the table's nameless line as it was typed into **Number Of
Blades** before.

### What comes out

Each dataset in the input is replaced by a multiblock of its copies, so the
input's own tree survives:

```
Root
└── Boundaries
    └── BLADE_quad
        ├── Copy 0        the input, where it already is
        ├── Copy 1        one pitch on
        └── …
```

Keeping the tree this way round means the Multi-block Inspector can still hide
`BLADE_quad` in every copy at once, and `BlockSelectors` paths gain a `/Copy 1`
on the end rather than changing shape. Each copy carries `CopyId`, constant
across the copy and put on whichever attribute the data already uses so it is
offered once in the coloring dropdown, and a `CopyAngle` field-data value.
Copying an already copied mesh — a sector of three, then twelve of those — adds
`CopyId2` and `CopyAngle2` rather than labelling over the first round.

Only the points of a copy are new. The cells and the arrays that were not
rotated are passed through by reference, so a full annulus costs one set of
coordinates per copy rather than one whole mesh.

### Rotating vectors

A velocity that was not turned with the geometry would point the wrong way in
every copy but the first, so **Rotate Vectors** is on by default and turns every
three-component array of floats — including the cell arrays, which is what the
reader produces with its **Cell Data to Point Data** unticked.

Three-component arrays of any other type are left where they are. Only a
floating-point triple can hold a rotated vector: a colour stored as three
unsigned bytes would come back with its components mixed and then wrapped around
by its own type, so `(10, 20, 30)` would turn into `(237, 10, 30)`.

Untick the box and nothing is touched at all — including the array ParaView has
marked as the active vectors, which `vtkTransformFilter` would otherwise rotate
on its own. Either way the copies agree with the input about which array is the
active vectors.

## Linear copies

A linear cascade is the flat cousin of a blade row: the mesh holds one passage
and the machine is that passage translated over and over, with no axis anywhere.
VECLYS writes one whenever `input.au3x` says `cosystem=cartesian`, and **Linear
Copy** is the filter for it. There is no separate button — the **Circumferential
Copy** button makes one of these when the case is cartesian, and says so:

```
Circumferential copy: /path/to/cases/cascade/input.au3x
  eleven: cartesian, pitch 0.05655
Circumferential copy: the periodic patches of eleven lie (0, -0.05655, 0) apart,
  which is the way the copies go.
Circumferential copy: a pitch of 0.05655, out of the case file.
Circumferential copy: made a Linear Copy instead. The case is cartesian, and a
  straight cascade has no axis to turn about.
```

The file gives the distance between two passages and not which way they lie, so
the direction is **measured off the mesh**: the two periodic patches, `LEFT_quad`
and `RIGHT_quad`, are one pitch apart by construction, so the vector between
their centroids is both the direction and a check on the number. Only those two
patches are read, which is a few hundred faces however big the volume is. A mesh
with no such pair — the sections have to *begin* with the word, so a duct's
`inv_left_quad` is not mistaken for one — is reported and the direction left for
you to set, and a case whose section gives no `pitch` at all is reported too
rather than having one invented for it.

The Properties panel is the circumferential one with the rotation taken out:

- **Translation Direction** — which way the copies go. Only the direction is
  taken from it, so `0 1 0` and `0 2 0` mean the same thing;
- **Blade Rows** — the same table as the circumferential filter's, with the
  distance between passages where the blade count would be, nameless line and
  all, so a case of several rows stays one filter exactly as an annular one
  does.

There is **no Full Annulus** here, and not merely a greyed-out one: a cascade is
a straight line of passages with no wheel to close, so how many of them to draw
is the only answer there is, and the tick is not on the panel to press. If a
case ever mixed annular and cartesian rows — none ever has, and a machine is one
or the other — the cartesian ones would be left out of the Circumferential
Copy's table rather than turned about an axis they have not got, which leaves
them where they are with a line in the **Output Messages** window saying so.

The output is the same tree of `Copy 0`, `Copy 1` and so on, each with its
`CopyId`; the field-data value is a `CopyOffset` in the mesh's own unit rather
than a `CopyAngle` in degrees. There is no **Rotate Vectors**, because the
question does not come up: sliding a mesh sideways leaves every velocity
pointing where it did, so nothing is touched at all.

The two filters are one walk over the input with a different step, so the walk
itself lives in `circumferential_copy.py` and the linear one borrows it — the
tree, the labels, the per-row table and the pass-by-reference of everything but
the points are all the same code.

## Flow properties

A CGNS file carries what the solver wrote and none of the quantities that come
from its derivatives. The **Flow Properties** button adds those. It leaves the
filter unapplied, because working out derivatives over half a million cells is
not something to start by accident.

Two dropdowns say which arrays to work from. **Velocity** lists the
three-component arrays in the input and **Density** the single-component ones,
and nothing else is offered, so neither can be pointed at a pressure by mistake.
Only **Q-Criterion** and **Vorticity Magnitude** start ticked, because they are
what any velocity field can give; everything else is opt-in.

**The density is the one to watch.** A CGNS file often has no density in it —
`r37.cgns` carries velocity, pressure, temperature, k and omega and no density
at all — and the **Density** box then lands on whichever array comes first,
which is why the two quantities that read it start unticked. Make one first with
a **Calculator** and pick it here:

```
Result Array Name:  Density
Expression:         Pressure/(287.058*Temperature)
```

That is the ideal gas law for air, with 287.058 J/(kg K) as its gas constant. It
is worth having anyway: **Surface Average** wants a density too, for its
mass-flow averaging.

The **Quantities** list is a set of checkboxes, like the reader's boundaries:

- **Q-Criterion** and **Lambda2** — the two usual vortex-core criteria. A core
  is where Q is positive, or where lambda2 is negative; contour either one to
  see the tip vortex and the passage vortex as surfaces. Q is
  `(||W||^2 - ||S||^2) / 2` with Frobenius norms, equivalently `-tr(G^2)/2`,
  where `G` is the velocity gradient and `S` and `W` are its symmetric and
  antisymmetric halves; lambda2 is the middle eigenvalue of `S^2 + W^2`. Both
  are the standard definitions and both are transpose-invariant, so which way
  round the gradient tensor is stored cannot change them. Q carries no extra
  divergence term for compressible flow, which is what ParaView's own
  **Gradient** filter reports as well;
- **Vorticity** and **Vorticity Magnitude** — the curl of the velocity, and how
  fast the fluid is spinning;
- **Helicity** — velocity dotted with vorticity, which distinguishes a vortex
  the flow runs along from one it crosses;
- **Divergence** — zero in an incompressible solution, so a map of it is a map
  of how well converged the continuity equation is;
- **Density Gradient Magnitude** and **Numerical Schlieren** — the shock
  picture. The schlieren image is `exp(-k |grad rho| / max |grad rho|)`, white
  where the density is uniform and black across a shock, with **Schlieren
  Exponent** as the `k` that decides how hard it darkens.

Everything comes from one pass of `vtkGradientFilter` per mesh, so
asking for six quantities costs little more than asking for one, and each lands
on the same attribute — points or cells — as the velocity or density it came
from. A quantity whose source array is missing is reported by name in the
**Output Messages** window and the others are still made.

So a full session on `r37.cgns` is: open the file, **Calculator** for the
density, press **Flow Properties**, tick what you want, **Apply**, then
**Contour** by `Q` at a positive value to see the tip vortex. From Python:

```python
gas = Calculator(Input=reader, ResultArrayName='Density',
                 Function='Pressure/(287.058*Temperature)')
properties = FlowProperties(Input=gas, Velocity='Velocity', Density='Density')
properties.Quantities = ['Q-Criterion', 'Lambda2', 'Numerical Schlieren']
```

## Radial profiles of a slice

The **Radial Profile** button cuts a plane through the middle of the machine,
normal to the axis it guesses from the input's points, puts a **Radial Profile**
on it, and opens a Line Chart View **across the bottom of the window** with the
radius along the foot of it and the flow properties up the side. Press it on a
slice you have already placed and it profiles that one instead, taking the
machine axis from the plane's own normal. This button applies what it makes,
as **Surface Average** does: a table of a few dozen rows exists to be looked at,
where a repeated volume is something to check the numbers of first.

The filter takes **Number Of Bands** radii across the span and contours the
input at each. That curve is where the band cuts the slice, and every property
on it is averaged **weighted by edge length** — the same integrator the surface
average uses, one dimension down. It matters for the same reason: the curve at
mid-span of the rotor is 210 edges whose lengths span a factor of 771,
tightening where the mesh does against the blade, and a mean over the points
would count that crowd of short edges as if each carried a full share of the
circumference. The output is a table: one row per band, a `Radius` column, a
`Length` column for the curve, and one column per property.

- **Rotation Axis** and **Axis Origin** — the machine axis. There is no guessing
  inside the filter: a planar slice looks narrow from any axis lying in it, so
  the axis is asked for rather than inferred;
- **Number Of Bands** — 50 by default;
- **Spacing** — **Clustered at the walls** by default, which is where a
  turbomachine's gradients are. The samples are the interior points of a cosine
  distribution, so over 50 bands the first sits about a two-thousandth of the
  span from the hub instead of a hundredth, and the middle of the span is
  sampled half as often. On the rotor at 30 mm that is the difference between a
  first point at 0.1867 m reading 309 K and one at 0.1852 m reading 329 K — the
  endwall boundary layer, which even spacing steps straight over. Switch to
  **Uniform** — the midpoints of equal bands — if the rows are going to be
  averaged back into one number, since only then do they carry equal weight;
- **Automatic Radius Range** — on by default, taking hub and casing from the
  input. Untick it to profile part of the span, or to hold the range still while
  comparing one slice with another;
Bands that miss the input are left out of the table rather than filled with
nothing, and how many were dropped is reported.

### One plot per station

Pressing the button again gives the next station a plot of its own beside the
last, rather than drawing over it, so a run of slices down the machine can be
compared at a glance:

```
Render view                        (the top 65% of the window)
────────────────────────────────
  X = 0     │  X = 0.03  │  X = 0.06
```

Each plot is titled with where its slice is, taken from the cutting plane, and
the row is kept evenly wide however many there are — halving the rightmost plot
each time would leave 1/2, 1/4, 1/4, so the splits are set from the top of the
row instead. To draw over the last plot rather than add one, change the word at
the top of `macros/Radial Profile.py`:

```python
_A_PLOT_EACH = False        # True by default
```

Each station keeps a plot of its own, and pressing the button on a profile is
what puts it there. That matters because ParaView's own eye icon draws a source
into whatever view is **active**, so making a profile visible from the Pipeline
Browser drops it onto whichever plot you last clicked -- usually the first
station's -- rather than reopening its own. Closed a station's plot, or found one
crowding another's? Select that profile and press the button: it is moved to a
plot of its own, and the one it was crowding is left as it was. The eye icon
stays ParaView's; the button is what keeps the stations apart.

Every property is drawn, less the `Radius` and `Length` columns, which are the
axis itself and the length the averaging weighted by. They all share the one
left-hand axis, so a pressure of 130,000 flattens a temperature of 320 against
the floor: untick in **Series** the ones you are not comparing, or send one to
the right-hand axis with **Series Plot Corner**.

The other way round — radius up the side, the way a spanwise profile is usually
drawn — is a word at the top of `macros/Radial Profile.py`:

```python
_RADIUS_ON = "y"        # "x" by default
```

That carries one property at a time, because the foot of the chart is then in
that property's own units and a pressure cannot share it with a temperature;
`Pressure` is chosen if the slice has one, then `Temperature`, `Density`,
`Mach`, and **X Array Name** in the **Display** section switches it.

Either way the series have to be named as the chart names them rather than as
the table does, because a vector column becomes several series — `Velocity_X`
and its fellows, with `Velocity_Magnitude` the one ParaView draws by default —
and a column name would match none of them.

## Integrating a slice

The **Surface Average** button reduces the selected slice to one row and opens
it in a **SpreadSheet View across the bottom of the window** — a handful of
scalars is what that view is for, and a full-height view down the right-hand
side would waste the space the machine is drawn in. Pressing it again reuses
that view rather than carving the window up a second time. It also writes the row
to the **Output Messages** window, which is quicker to read than a table when
all you wanted was the mass flow.

Every row says which surface it is. A multiblock input — a slice of several
zones, or a set of boundary patches — gives **one row per block** by default,
each named in the `Surface` column by its path in the Multi-block Inspector, and
a `Centre` column puts it in space, which is how one axial station is told from
another. **Rows** set to *Everything Together* adds the blocks into a single
row instead. The button also names the filter after what it was made from, so
the Pipeline Browser and the SpreadSheet View's own heading say `Surface Average
of Slice1` rather than `SurfaceAverage1`.

```
Surface                 Area       Centre                    Pressure
Boundaries/HUB_quad     0.004655   (0.0324, 0.0020, 0.1827)   106850
Boundaries/CASING_quad  0.006485   (0.0324, 0.0019, 0.2456)   112020
```

**Weighting** decides what the row holds:

- **Integral** — the plain integral of each property over the surface;
- **Area Average** — that divided by the area, which is the default. On a solid
  rather than a surface it is a volume average, and the first column says
  `Volume` rather than `Area` so the table cannot lie about it;
- **Mass Flow Average** — each point weighted by the mass crossing the surface
  there, which is how a stagnation quantity should be averaged. It needs a
  **Density** and a **Velocity** array, and it adds a `MassFlow` column. The
  surface normal comes from the geometry, so the sign of the flux follows
  whichever way the normals happen to point; the mass flow is reported positive
  and the averages are unaffected, being ratios. Cell arrays are moved onto the
  points first, so every column is weighted the same way.

Both filters integrate through `vtkIntegrateAttributes`, the same code behind
ParaView's own **Integrate Variables**, so every cell counts for its own area
and not for one cell's worth. That matters: a slice of the rotor is 12,468
triangles whose areas span a factor of 1,200, and an unweighted mean of them
would be dominated by whichever corner of the passage happens to be finely
meshed. Two triangles of area 0.5 and 50 carrying 1 and 100 average to 99.02,
not to 50.5.

A point array is integrated over each triangle with the linear interpolation
that filter uses — the area times the mean of the three corners — rather than
being collapsed to one value per cell first, so a field that varies across a
triangle is integrated exactly rather than approximately.

## Plotting CSV files together

The profile and the averages write csv, and a rig or another run gives csv, so
**Plot CSV Files** opens several at once and draws them on one chart -- profiles
to compare, measurements beside them, whatever was written to columns. Press the
button, pick the files of a set, and each is opened and plotted.

Each file is drawn against its own radius column -- found by name (`radius`,
`r`, `span` and their like), or the first column if none matches -- and its
series are named after the file, so `runA: Pressure` and `runB: Pressure` are
told apart in the legend. The charts open in a **tab of their own** -- named
"CSV Files" -- so they never crowd the render view or a profile's plot.

**A plot per press.** Press the button again with another set and that set gets
its own chart beside the first, evenly wide, so several sets sit side by side to
be read against each other. Each chart is titled after the folder its set came
out of, or after the first file when they came from several; two sets out of one
folder are told apart by qualifying the title, since a row of charts all called
`steady` is no way to know which is which. Set `_A_PLOT_EACH` to `False` at the
top of the macro to go back to one chart, cleared and redrawn each press.

**A file can go on as many charts as it belongs on.** Picking one that is
already open draws the source already there rather than opening the file again,
so it stays a single entry in the Pipeline Browser under the name it was given,
and the same measurement can sit on every chart it is worth comparing against.
It is re-read as it is picked, since a file worth picking again is often one
that has been written again; a reader that will not re-read says so rather than
letting yesterday's numbers pass for today's.

The picker is the desktop's, through `zenity`, because ParaView's Python has no
Qt in it for a macro to open a window with. Without it, open the csv files
yourself with **File > Open**, select
one in the Pipeline Browser, and press the button; it plots that one. This is a
plain table plot -- nothing turbomachinery-specific -- so it works for any csv
of a column to plot against and columns to plot.

## Supported CGNS layouts

The two common ways of storing boundary faces are handled, and the plugin picks
per section which one applies:

- **Separate boundary base** (`bfs.cgns`): the face sections live in their own
  base whose `CellDimension` is one below the volume mesh. ParaView's CGNS
  reader loads those sections as cells, so each section is a plain cell range of
  that base's grid, and it carries that base's own `FlowSolution`.
- **Faces inside the volume zone** (`r37.cgns`): the face sections share the
  zone with the volume elements and continue its element numbering. ParaView's
  reader keeps only the volume cells of such a zone and drops those sections
  entirely, so the faces are rebuilt from the CGNS `ElementConnectivity` against
  the zone's points. Because the file stores no solution on the faces
  themselves, each face inherits the cell data of the volume cell it bounds.

Sections of an element type that cannot be rebuilt from a fixed-stride
connectivity (for example `MIXED`) are reported by name rather than dropped
silently.

## Verified results

For `bfs.cgns`, six boundaries and 58,672 faces beside a 633,600-cell volume:

- `B0_quad_inlet`: 2,240
- `B1_quad_exit`: 3,776
- `B2_quad_LEFT`: 19,800
- `B3_quad_RIGHT`: 19,800
- `B4_quad_vwall`: 7,296
- `B5_quad_top`: 5,760

For `r37.cgns`, seven boundaries and 46,184 faces beside a 493,232-cell volume:

- `BLADE_quad`: 11,656
- `RIGHT_quad`: 4,524
- `LEFT_quad`: 4,524
- `INLET_quad`: 3,596
- `EXIT_quad`: 3,596
- `HUB_quad`: 8,216
- `CASING_quad`: 10,072

In both cases the boundary faces merge into a closed manifold shell with zero
free edges, which is what proves the reconstruction is geometrically right.

## Headless verification

```bash
pvpython ./verify_plugin.py ./r37.cgns
```

The script takes any CGNS file and defaults to `bfs.cgns`. It reads the expected
section names, element ranges, and volume cell counts out of the file's own HDF5
tree rather than comparing against hardcoded numbers, then checks the checkbox
lists, the per-block contents, the closure of the boundary shell, that each
array is offered on one attribute only in either mode, and that unticking a
boundary really removes it.

The VECLYS reader has its own check, which reads `input.au3x` files supplied as
arguments, or in child folders under `PARAVIEW_DIY_CASES_DIR`:

```bash
pvpython ./verify_veclys_reader.py /path/to/cases/stage/input.au3x
```

It works every row's CGNS path out of the input file a second way, with a regex
rather than the reader's own parser, and compares the two; it opens each of
those meshes directly with the CGNS reader and compares the block trees cell for
cell; it checks that the device-list parser handles several chains and a level
with no type on it; that unticking a row leaves only the others, without
renaming them; that ticking a boundary reaches the reader inside; that a
case number nobody ran is reported rather than raised; and that a case opened
before its mesh was written picks the mesh up on Reload, rather than settling
into nothing. The optional three-row E3 stage reference is
included when configured, so the blade counts 32, 28 and 50 are checked to
land against the rows they belong to.

The coloring needs a render window, so it has its own check:

```bash
pvbatch --force-offscreen-rendering ./verify_auto_color.py
```

It confirms that each file is colored by a point array without being asked, that
no `vtkBlockColors` bar is left visible, that unrelated sources are left alone,
that a manual choice is never overridden, and that the shared color map covers
every open file.

The circumferential copies have their own check, which also takes any CGNS file
and defaults to `r37.cgns`:

```bash
pvpython ./verify_circumferential_copy.py
```

Every copy is compared against a rotation worked out here with numpy, and, where
the file has the periodic pair of a passage, against the mesh itself: one pitch
of 360 / 36 must carry `LEFT_quad` onto `RIGHT_quad`, which it does to within
2.6e-08 on a mesh 0.149 across. It also checks the block tree, `CopyId` and
`CopyAngle`, that a blade count of −48 turns the copies the other way by 7.5°,
that **Full Annulus** closes a 24-blade row at 24 copies, that a block a **Blade
Rows** line names turns by its own 360 / 12 three times while its neighbour
stays on the nameless line's 360 / 36 twice, that a line with its copies cell
left empty keeps the nameless line's count while taking its own pitch, that a
line naming nothing in the input changes nothing and says so, that a block the
table leaves with no pitch is copied once where it is and named, that a table
nobody could act on — two lines for one row, a cell that is not a number, half a
blade — is refused by the line it is on and draws nothing rather than something
wrong, that **Rotate Vectors** really leaves the arrays alone
when it is off while the active-vectors mark survives either way, that an
integer triple comes through a quarter turn unchanged while the normals beside
it turn, that the raw cell arrays rotate as the point arrays do, that copying a
copy keeps both rounds of labels, that a plain vtkPolyData input works as well
as a multiblock, and that the toolbar macro guesses the axis on the first press
and inherits the settings on the second.

Where the pitch comes from, and the Linear Copy the cartesian half of it needs,
have a check of their own:

```bash
pvpython ./verify_passage_copies.py
```

It reads four real runs and compares what comes out against the pitches worked
out by hand — the E3 stage's 11.25°, 12.857° and 7.2°, rotor 37's 10°, the STCF
cascade's 0.05655 and the duct that gives none — and checks that the duct's
stale `assembly-sectors=12` is *not* offered as a blade count, which is the one
mistake reading `cosystem` first exists to prevent. Then the filter: a sphere
copied along an unnormalised direction lands where numpy says, a negative pitch
copies the other way, a span of 1.0 at a pitch of 0.25 gives four copies, the
normals come through untouched, and a section a **Blade Rows** line names slides
by its own 0.05 twice where its neighbour takes the nameless line's 0.01 three
times.
Then the button, pressed on each case in turn:

- rotor 37, one row, gives one Circumferential Copy whose table is the single
  line `r37 36 4`, with **Full Annulus** left for you to tick — and ticking it
  gives 36 copies;
- the E3 stage gives one filter whose **Blade Rows** table reads
  `IGV 32 5`, `R1 28 4`, `S1 50 7` — four copies of the widest-pitched row and
  as many of the others as it takes each to reach the same place — and it comes
  out as exactly that, with every row turning by its own pitch. One tick of
  **Full Annulus** gives 32, 28 and 50;
- the cascade gives a **Linear Copy** whose pitch and measured direction carry
  `LEFT_quad` onto `RIGHT_quad` to within 5e-13, and which has no **Full
  Annulus** on it to press;
- the duct, which has neither a pitch in its file nor a periodic pair in its
  mesh, gives a Linear Copy left at its defaults with a warning rather than a
  number nobody can justify.

Each case is skipped, with a line saying so, when it is not on the machine.

The flow properties and the slice averages are checked against answers that are
known before the code runs:

```bash
pvbatch --force-offscreen-rendering ./verify_flow_tools.py
```

- a solid body turning at 2 rad/s with a drift of 3 m/s along the axis has a
  vorticity of `(0, 0, 4)`, a lambda2 of `-4`, a Q of `4`, a helicity of `12`
  and no divergence, and the filter gets every one of them exactly;
- a solid body has no strain, though, so it cannot tell one convention for Q
  from another. A second field is built from a general asymmetric tensor `A`
  with a non-zero trace, giving a compressible flow with both strain and spin,
  and every quantity is compared against `A` worked out with numpy: Q is 1.15
  both as `(||W||^2 - ||S||^2)/2` and as `-tr(A^2)/2`, lambda2 is -1.03486471,
  the vorticity is `(0.7, 0.1, 3)` and the divergence is 0.2, each to within
  2.4e-15;
- the density gradient of `x^2` is `2|x|` wherever a central difference can be
  taken, and the schlieren picture is the exponential of it;
- a plane cut into radial bands gives curves that are circles: their length is
  within 3% of `2 pi r` — a polygon through a grid of unit cells falls a little
  short of the arc — and a field that is as positive as it is negative round the
  circle averages to `1e-17`, which is what the edge-length weighting is for;
- clustering the same bands keeps them inside the span and symmetric about
  mid-span, and draws them into the walls: the gap at the wall is 0.038 of the
  span against 0.383 in the middle. The circles are still circles wherever they
  are cut;
- a 10 by 10 square has an area of 100, a constant 3 integrates to 300 over it,
  and 2 kg/m3 at 5 m/s through it is 1000 kg/s;
- two triangles of area 0.5 and 50 carrying 1 and 100 average to 99.0198, which
  is what their areas call for and not the 50.5 an unweighted mean would give,
  for a cell array and a point array alike;
- two edges of length 1 and 100 carrying 1 and 100 average to the same 99.0198
  along a curve, and point values of 0, 0 and 100 along them to 49.5050 rather
  than the 33.3 a mean of the points would give;
- with the density sloping across the plane the mass-flow average of `Y` parts
  company with the area average and lands within 3% of the 5/6 worked out by
  hand, the gap being the quadratic product integrated with the same linear
  rule as everything else.

Then rotor 37 is asked the questions arithmetic cannot answer:

- at 30 mm the mass-flow integral over one passage is 0.5755 kg/s, so the 36
  passages pass 20.72 kg/s against the machine's design 20.19 kg/s;
- the same passage at x = 0 carries 0.57576 kg/s and at x = 30 mm 0.57554 kg/s,
  differing by 0.04%. Nothing in the code makes mass conserve itself between two
  stations; the integration, the normals and the sign have all to be right;
- the pressure rises from 121.0 kPa at the hub to 134.4 kPa at the casing;
- the 10 radial bands average to 129,975 Pa where the whole slice averages to
  129,962 Pa, so the profile and the integral agree to 0.01%;
- the hub and casing patches come out named, with the hub's centre 0.1827 m from
  the axis and the casing's 0.2456 m, and their areas add up to what the two
  make together.

The three buttons are then pressed in turn. The chart has to be plotted against
`Radius` rather than the row index, with its foot labelled, not to plot its own
bookkeeping columns, and to carry the velocity that a column name alone would
have missed; the chart
and the spreadsheet have to be reachable from the root of the layout through
top-and-bottom splits only, since one side-by-side split would put them down the
right; and a second press of Surface Average has to reuse its view rather than
split the window again, while a second press of Radial Profile has to do the
opposite: a plot of its own, beside the first, titled with its own station, the
two of them sharing the row evenly. Plot CSV Files has to open the files it is
given, draw each on a chart in its own tab against its own radius column, and
name every series after its file; a second set has to get a chart of its own
beside the first, under a title the row does not already carry, with the first
still drawn; and a third set holding a file from the first has to draw the
source already open rather than leave two readers of one file — the file used
again comes out on the first chart and the third, from one reader. The picker is
stood in for by a zenity that answers out of the environment, so the suite never
waits on a screen.

## How it works

The VECLYS reader owns a CGNS reader per blade row rather than inheriting from
one: ParaView's plugin decorators hand back a function that builds the class
rather than the class itself, so a registered reader cannot be a base class, and
driving one from inside is what this plugin does with `vtkCGNSReader` anyway. It
keeps its own checkbox lists, gathered from every row and handed back down to
the row each name came from.

Only CGNS/HDF5 section metadata is read directly. The mesh and the solution
arrays come from ParaView's own `vtkCGNSReader`, which the plugin drives
internally, so array naming and vector assembly behave exactly as they do in the
stock reader. The derived quantities come from `vtkGradientFilter` and the
averages from `vtkIntegrateAttributes`, so they agree with what ParaView's own
**Gradient** and **Integrate Variables** filters would say.

Each file in `plugins/` is one filter or reader, loadable on its own. The VECLYS
reader is the one that imports a neighbour, because it drives a CGNS
labeled-boundary reader per blade row; ParaView loads a plugin by its path, so
it puts its own directory on `sys.path` and imports the other by name.

Everything shared lives in `pylib/`, which the launcher puts on `PYTHONPATH` and
every plugin adds for itself. ParaView runs a macro as a bare script, with no
`__file__` to find anything by, so this is the only place a macro and a plugin
can meet:

- `passage_copies.py` — repeating a passage: the walk over the input, the table
  of blade rows, the labels each copy carries, and the pass-by-reference of
  everything but the points. Circumferential Copy and Linear Copy are that
  machinery plus a step — a rotation, a translation — so neither is a copy of
  the other and neither owns the shared half;
- `veclys_case.py` — what an `input.au3x` says. The reader finds its meshes with
  it and the copy button reads the pitch with it, so the plugin and the button
  cannot come to different conclusions about the same file;
- `paraview_diy_macros.py` — what the toolbar buttons have in common: guessing
  the machine axis, walking up a pipeline to the case it came from, measuring a
  mesh's periodic offset, finding the previous filter of a kind, warning in a
  way that opens the Output Messages window.
