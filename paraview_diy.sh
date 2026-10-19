#!/usr/bin/env bash
set -euo pipefail

# Wherever this script is, that is the checkout: plugins/, macros/ and pylib/
# are beside it. Nothing here has to be edited to run it somewhere else.
DIY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# PARAVIEW_ROOT selects an installation explicitly. Otherwise use PATH, then
# look for an unpacked distribution in common user and system locations.
if [ -z "${PARAVIEW_ROOT:-}" ]; then
  ON_PATH="$(command -v paraview || true)"
  if [ -n "$ON_PATH" ]; then
    PARAVIEW_ROOT="$(cd -- "$(dirname -- "$(readlink -f "$ON_PATH")")/.." && pwd -P)"
  fi
fi
if [ -z "${PARAVIEW_ROOT:-}" ]; then
  for PV_CANDIDATE in "$HOME"/ParaView-* "$HOME"/Downloads/ParaView-* \
      "$HOME"/tools/ParaView-* /opt/ParaView-*; do
    if [ -x "$PV_CANDIDATE/bin/paraview" ]; then
      PARAVIEW_ROOT="$PV_CANDIDATE"
      break
    fi
  done
fi
if [ -z "${PARAVIEW_ROOT:-}" ] || [ ! -x "$PARAVIEW_ROOT/bin/paraview" ]; then
  echo "VECLYS Postprocessor: no ParaView found. Put its bin/ on PATH, or set"\
       "PARAVIEW_ROOT to the installation directory." >&2
  exit 1
fi

# ParaView is an MPI build and carries its own MPI in lib/. A different MPI on
# the environment -- the NVIDIA HPC SDK's, a Fluent or system one -- shadows it
# and the GUI aborts at startup with an "Invalid communicator" MPI_Abort. So the
# foreign MPI is taken off PATH and ParaView's own lib is put first on the
# library path, leaving ParaView to use the MPI it was built with.
CLEAN_PATH=""
while IFS= read -r part; do
  case "$part" in
    */nvidia/hpc_sdk/*|*/Fluent.Inc/*|*/ParaView-*/bin) : ;;   # drop foreign MPI / other ParaViews
    "") : ;;
    *) CLEAN_PATH="${CLEAN_PATH:+$CLEAN_PATH:}$part" ;;
  esac
done <<< "$(printf '%s' "$PATH" | tr ':' '\n')"
export PATH="$PARAVIEW_ROOT/bin:$CLEAN_PATH"
export LD_LIBRARY_PATH="$PARAVIEW_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# ParaView auto-loads every plugin it finds on this path.
export PV_PLUGIN_PATH="$DIY_ROOT/plugins"

# The macros import what they have in common from here.
export PYTHONPATH="$DIY_ROOT/pylib${PYTHONPATH:+:$PYTHONPATH}"

# ParaView turns every file in its Macros directory into a button on the Macros
# toolbar, which is the only way a Python add-on gets one. The links are left
# pointing at the repository, so editing a macro here is enough.
MACRO_DIR="${HOME}/.config/ParaView/Macros"
mkdir -p "$MACRO_DIR"
for MACRO in "$DIY_ROOT"/macros/*.py; do
  NAME="$(basename "$MACRO")"
  if [ -L "$MACRO_DIR/$NAME" ] || [ ! -e "$MACRO_DIR/$NAME" ]; then
    ln -sfn "$MACRO" "$MACRO_DIR/$NAME"
  else
    # Somebody's own macro is sitting there; leave it alone rather than lose it.
    echo "VECLYS Postprocessor: $MACRO_DIR/$NAME is not a link, leaving it as it is." >&2
  fi
done

# The plugin does the reading; this only picks the first coloring, which
# ParaView otherwise leaves as Solid Color.
exec "$PARAVIEW_ROOT/bin/paraview" \
  --script "$DIY_ROOT/auto_color_cgns.py" "$@"
