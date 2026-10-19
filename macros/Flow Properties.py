"""ParaView macro: put a Flow Properties filter on the active source.

Left unapplied, like picking the filter from the menu: the quantities are worked
out from derivatives over the whole mesh, which is not something to start by
accident on half a million cells, and the velocity and density arrays are worth
a glance first.
"""

from paraview import simple as pvs

try:
    import paraview_diy_macros as diy
except ImportError:
    # Normally a loaded plugin has already put pylib on the path, and
    # paraview_diy.sh puts it there too.
    print("Flow properties: load the plugins first -- Tools > Manage Plugins, "
          "or start ParaView with paraview_diy.sh.")
    raise

_LABEL = "Flow properties"
_FILTER = "FlowProperties"
_INHERITED = ["Velocity", "Density", "SchlierenExponent"]


def main():
    build = diy.filter_factory(_LABEL, _FILTER)
    if build is None:
        return
    source = diy.active_source(_LABEL, reject=(_FILTER,))
    if source is None:
        return

    properties = build(Input=source)
    template = diy.previous_filter(_FILTER)
    if template is not None:
        diy.inherit(properties, template, _INHERITED)
        properties.Quantities = list(template.Quantities)
        print("%s: reusing the settings of the previous one." % _LABEL)

    pvs.SetActiveSource(properties)
    print("%s: tick the quantities you want, then click Apply." % _LABEL)


main()
