#!/bin/bash
#
# Windows-friendly variant of create_blender_dist.sh.
#
# The original script assumes a POSIX virtualenv layout (.venv/bin/...) and an
# external `zip` binary, neither of which exists in a stock Git Bash + uv setup
# on Windows. This one drives uv directly and builds the archive with Python's
# zipfile module instead.
#
# Run it from Git Bash in the repository root:
#
#     bash etc/scripts/create_blender_dist_windows.sh
#
# The resulting ZIP lands in dist/ and can be installed with
# Blender -> Edit -> Preferences -> Add-ons -> Install from Disk.

set -e

SCRIPT_ROOT=$(dirname "$0")
REPO_ROOT="${SCRIPT_ROOT}/../.."
cd "${REPO_ROOT}"

VERSION=$(grep ^version pyproject.toml | head -1 | cut -d '"' -f 2)
STEM="skybrush-studio-for-blender-${VERSION}"
BUILD="dist/build"
REQ="dist/requirements.txt"

echo "--> Collecting dependencies..."
mkdir -p dist
rm -rf "${BUILD}"
mkdir -p "${BUILD}/vendor/skybrush"
uv export --no-dev --no-hashes --no-emit-project --format requirements-txt >"${REQ}"
trap 'rm -f "${REQ}"' EXIT
uv pip install -q -r "${REQ}" -t "${BUILD}/vendor/skybrush"

echo "--> Copying addon code..."
cp -r src/modules/sbstudio "${BUILD}/vendor/skybrush/"
cp src/addons/ui_skybrush_studio.py "${BUILD}/"

echo "--> Cleaning up..."
rm -rf "${BUILD}"/vendor/skybrush/bin "${BUILD}"/vendor/skybrush/*.dist-info
find "${BUILD}" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

echo "--> Creating ZIP..."
BUILD="${BUILD}" OUT="dist/${STEM}.zip" python - <<'PYTHON'
import os, pathlib, shutil, zipfile

build = pathlib.Path(os.environ["BUILD"])
out = pathlib.Path(os.environ["OUT"])
out.unlink(missing_ok=True)

files = sorted(p for p in build.rglob("*") if p.is_file())
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for path in files:
        # Entries must sit at the root of the archive, with no wrapping
        # directory: ui_skybrush_studio.py resolves "vendor/skybrush" relative
        # to itself, and Blender only recognizes a bare .py file or a package
        # with an __init__.py as an add-on.
        z.write(path, path.relative_to(build).as_posix())

shutil.rmtree(build)

with zipfile.ZipFile(out) as z:
    names = z.namelist()

assert "ui_skybrush_studio.py" in names, "add-on entry point is missing from the archive root"
assert any(n.startswith("vendor/skybrush/sbstudio/") for n in names), "sbstudio module is missing"

print(f"    {out} ({out.stat().st_size / 1024 / 1024:.2f} MB, {len(names)} files)")
PYTHON

echo ""
echo "Bundle created successfully in dist/${STEM}.zip"
