"""What an ``input.au3x`` says about the run it describes.

The VECLYS reader needs the file to find the meshes; the copy macro needs it to
know how a passage repeats. Both readings are here, so the plugin and the
toolbar button agree about what the file means.

Two keys of a ``[<row>]`` section decide the repeat:

``cosystem``
    ``annular`` for a machine with an axis, ``cartesian`` for a linear cascade.
``assembly-sectors`` / ``pitch``
    the blade count of an annular row, so its pitch is ``360 / count``; the
    distance between two passages of a cartesian one, in the mesh's own units.

The coordinate system has to be read first, because ``assembly-sectors`` is left
behind in a cartesian section as whatever it last was -- ``half_cylinder`` is a
straight duct carrying ``assembly-sectors=12`` -- and taking it at face value
there would invent a 30 degree pitch for a mesh that has no axis to turn about.
"""

import os

_DEVICE_LIST = "devicelist"
_DEVICE = "device"

ANNULAR, CARTESIAN = "annular", "cartesian"


def read_sections(file_name):
    """The ini file as {section: {key: value}}.

    Written out rather than handed to configparser because the values are full
    of colons -- ``empty:rotor37-dom:r37`` -- and configparser would take the
    first of them for the separator it also accepts.
    """
    sections, current = {}, None
    with open(file_name, "r") as handle:
        for line in handle:
            line = line.split("#", 1)[0].split(";", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                sections.setdefault(current, {})
            elif "=" in line and current is not None:
                key, _, value = line.partition("=")
                sections[current][key.strip()] = value.strip()
    return sections


def device_chains(devicelist):
    """The device tree as chains of names, root first.

    A level is ``type:name``, or a bare name for xcode's ``empty`` type. Only
    the names matter here: they are the folders.
    """
    chains = []
    for token in devicelist.split(","):
        names = []
        for part in token.split("-"):
            part = part.strip()
            if not part:
                continue
            kind, separator, name = part.partition(":")
            names.append(name.strip() if separator else kind)
        if names:
            chains.append(names)
    return chains


def _number(settings, key, default=None):
    """A setting as a float, or the default when it is missing or not one."""
    try:
        return float(settings[key])
    except (KeyError, TypeError, ValueError):
        return default


class Row(object):
    """One device of a case: where its mesh is, and how its passage repeats."""

    def __init__(self, name, mesh, settings):
        self.name = name
        self.mesh = mesh
        self.settings = settings
        self.cosystem = (settings.get("cosystem") or "").strip().lower()

    @property
    def annular(self):
        return self.cosystem == ANNULAR

    @property
    def cartesian(self):
        return self.cosystem == CARTESIAN

    @property
    def grid_sectors(self):
        """How many passages the mesh itself holds. One, in every run so far."""
        count = _number(self.settings, "grid-sectors", 1.0)
        return int(count) if count and count >= 1 else 1

    @property
    def blades(self):
        """The blade count of the row, or None if it is not an annular one."""
        if not self.annular:
            return None
        count = _number(self.settings, "assembly-sectors")
        return int(count) if count and count >= 1 else None

    @property
    def pitch(self):
        """How far one passage is from the next.

        Degrees about the machine axis for an annular row, and the mesh's own
        length unit along the cascade for a cartesian one -- two different
        quantities, which is why :attr:`cartesian` has to be consulted before
        the number is used. None when the file does not say.
        """
        if self.annular:
            blades = self.blades
            return None if blades is None else 360.0 * self.grid_sectors / blades
        if self.cartesian:
            return _number(self.settings, "pitch")
        return None

    def describe(self):
        """One line saying what the file gives for this row."""
        if not self.cosystem:
            return "%s: no cosystem in the file" % self.name
        if self.annular:
            if self.blades is None:
                return "%s: annular, but no assembly-sectors to take a pitch from" % self.name
            held = "" if self.grid_sectors == 1 else " over %d passages" % self.grid_sectors
            return ("%s: annular, %d blades%s, pitch %.6g deg"
                    % (self.name, self.blades, held, self.pitch))
        if self.cartesian:
            if self.pitch is None:
                return "%s: cartesian, but no pitch in the file" % self.name
            return "%s: cartesian, pitch %.6g" % (self.name, self.pitch)
        return "%s: cosystem %s, which is neither annular nor cartesian" % (
            self.name, self.cosystem)


def case_rows(input_file, case_number):
    """[Row] for every device the case describes.

    Every device gets a folder of its own name, every device gets a folder for
    the case number inside that, and the leaf writes its mesh there under its
    own name -- so ``empty:rotor37-dom:r37`` with case 0000 is
    ``rotor37/r37/0000/r37.cgns``, beside the input file.
    """
    sections = read_sections(input_file)
    devicelist = sections.get(_DEVICE_LIST, {}).get(_DEVICE, "")
    if not devicelist:
        raise RuntimeError("%s has no [%s] %s to say what the case contains."
                           % (os.path.basename(input_file), _DEVICE_LIST, _DEVICE))
    beside = os.path.dirname(os.path.abspath(input_file))
    rows = []
    for names in device_chains(devicelist):
        leaf = names[-1]
        folder = os.path.join(beside, *(names + [case_number]))
        rows.append(Row(leaf, os.path.join(folder, leaf + ".cgns"),
                        sections.get(leaf, {})))
    return rows


def case_meshes(input_file, case_number):
    """[(device name, CGNS path)] for every domain the case describes."""
    return [(row.name, row.mesh) for row in case_rows(input_file, case_number)]
