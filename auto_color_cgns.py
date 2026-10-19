"""ParaView startup script: color CGNS labeled-boundary sources by point data.

The plugin already interpolates the CGNS cell arrays onto the points, but
ParaView paints every new source Solid Color, so the result is invisible until
someone picks an array from the Coloring dropdown. This script makes that first
choice automatically, once per source, and then stays out of the way: change the
coloring afterwards and it is never touched again.

Set PARAVIEW_DIY_COLOR_BY to prefer a different array, for example

    PARAVIEW_DIY_COLOR_BY=Temperature
"""

import builtins
import os

import vtk
from paraview import servermanager
from paraview import simple as pvs


# Both readers of this project's making: a VECLYS case is the same mesh, opened
# by the file that describes the run rather than by the file it wrote.
_READER_XML_NAMES = ("CGNSLabeledBoundaryReader", "VeclysReader")
# First one present wins; anything else falls back to the first point array.
_PREFERRED_ARRAYS = [name for name in
                     os.environ.get("PARAVIEW_DIY_COLOR_BY", "").split(",") if name]
_PREFERRED_ARRAYS += ["Pressure", "Temperature", "Density", "Mach"]

_STATE = {"busy": False, "colored": set(), "ranges": {}, "views": {}}


def _source_id(source):
    try:
        return source.SMProxy.GetGlobalIDAsString()
    except AttributeError:
        return str(id(source.SMProxy))


def _point_array_names(source):
    point_data = source.PointData
    return [point_data[index].Name for index in range(len(point_data))]


def _choose_array(names):
    for preferred in _PREFERRED_ARRAYS:
        if preferred in names:
            return preferred
    return names[0]


def _rescale(source, array_name):
    """Widen the color map to cover this source as well as earlier ones.

    ParaView's own extend flag is overridden by the color map's rescale mode, so
    the union is tracked here and applied directly.
    """
    array_info = source.GetPointDataInformation().GetArray(array_name)
    if array_info is None:
        return
    component = -1 if array_info.GetNumberOfComponents() > 1 else 0
    low, high = array_info.GetComponentRange(component)
    previous = _STATE["ranges"].get(array_name)
    if previous is not None:
        low, high = min(low, previous[0]), max(high, previous[1])
    _STATE["ranges"][array_name] = (low, high)
    pvs.GetColorTransferFunction(array_name).RescaleTransferFunction(low, high)
    pvs.GetOpacityTransferFunction(array_name).RescaleTransferFunction(low, high)


def _stop_block_colors():
    """Keep ParaView from coloring these sources by vtkBlockColors on Apply.

    Otherwise every multiblock source comes up colored by its block index and
    leaves a vtkBlockColors color bar sitting in the render view even after the
    coloring is changed to a real array.
    """
    try:
        settings = pvs.GetSettingsProxy("GeneralSettings")
        if settings.ColorByBlockColorsOnApply:
            settings.ColorByBlockColorsOnApply = 0
    except Exception as error:
        print("VECLYS Postprocessor: could not turn off block coloring: %s" % error)


def _color_new_sources():
    """Color every CGNS source that nobody has colored yet."""
    changed = False
    for _registration, source in list(pvs.GetSources().items()):
        if source.GetXMLName() not in _READER_XML_NAMES:
            continue
        identifier = _source_id(source)
        if identifier in _STATE["colored"]:
            continue
        names = _point_array_names(source)
        if not names:
            # Apply has not produced data yet; try again on the next render.
            continue
        array_name = _choose_array(names)
        for view in pvs.GetViews():
            representation = servermanager.GetRepresentation(source, view)
            if representation is None:
                continue
            pvs.ColorBy(representation, ("POINTS", array_name))
            _rescale(source, array_name)
            representation.SetScalarBarVisibility(view, True)
            # Drops the vtkBlockColors bar left over from Apply.
            pvs.HideUnusedScalarBars(view)
            _STATE["colored"].add(identifier)
            changed = True
            print("VECLYS Postprocessor: coloring %s by point %s" %
                  (_registration[0], array_name))
    return changed


def _on_render(_caller, _event):
    if _STATE["busy"]:
        return
    _STATE["busy"] = True
    try:
        if _color_new_sources():
            pvs.Render()
    except Exception as error:
        print("VECLYS Postprocessor: could not set the default coloring: %s" % error)
    finally:
        _STATE["busy"] = False


def _watch_views():
    """Apply finishes after the proxies are registered, so act on the render."""
    for view in pvs.GetViews():
        try:
            identifier = view.SMProxy.GetGlobalIDAsString()
            window = view.SMProxy.GetClientSideObject().GetRenderWindow()
        except AttributeError:
            continue
        if window is None or identifier in _STATE["views"]:
            continue
        tag = window.AddObserver(vtk.vtkCommand.StartEvent, _on_render)
        _STATE["views"][identifier] = (window, tag)


def _registered(_caller, _event):
    _watch_views()


manager = servermanager.ProxyManager().SMProxyManager
previous = getattr(builtins, "_paraview_diy_color_observer", None)
if previous:
    previous_manager, previous_tag = previous
    try:
        previous_manager.RemoveObserver(previous_tag)
    except Exception:
        pass

observer_tag = manager.AddObserver(vtk.vtkCommand.RegisterEvent, _registered)
builtins._paraview_diy_color_observer = (manager, observer_tag)
_stop_block_colors()
_watch_views()
print("VECLYS Postprocessor: automatic point-data coloring is active")
