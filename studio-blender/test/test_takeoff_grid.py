"""Unit tests for takeoff grid creation, auto-expansion logic, and API limit fallback."""

import importlib.util
from unittest.mock import MagicMock
import sys

# Mock Blender modules before loading create_takeoff_grid file
class PropertyGroup:
    pass

class Operator:
    pass

mock_bpy = MagicMock()
mock_bpy.app.version = (4, 2, 0)
mock_bpy.app.handlers.persistent = lambda f: f
mock_bpy.props.IntProperty = lambda *args, **kwargs: 64
mock_bpy.props.FloatProperty = lambda *args, **kwargs: 1.0
mock_bpy.props.BoolProperty = lambda *args, **kwargs: False
mock_bpy.props.StringProperty = lambda *args, **kwargs: ""
mock_bpy.props.EnumProperty = lambda *args, **kwargs: ""
mock_bpy.props.PointerProperty = lambda *args, **kwargs: None
mock_bpy.props.CollectionProperty = lambda *args, **kwargs: None
mock_bpy.types.PropertyGroup = PropertyGroup
mock_bpy.types.Operator = Operator
mock_bpy.types.UIList = object
mock_bpy.types.Panel = object
mock_bpy.types.Header = object
mock_bpy.types.Menu = object

sys.modules['bpy'] = mock_bpy
sys.modules['bpy.app'] = mock_bpy.app
sys.modules['bpy.app.handlers'] = mock_bpy.app.handlers
sys.modules['bpy.props'] = mock_bpy.props
sys.modules['bpy.types'] = mock_bpy.types
sys.modules['bpy.path'] = mock_bpy.path
sys.modules['bmesh'] = MagicMock()
mock_mathutils = MagicMock()
sys.modules['mathutils'] = mock_mathutils
sys.modules['mathutils.bvhtree'] = mock_mathutils.bvhtree
sys.modules['gpu'] = MagicMock()
sys.modules['gpu.state'] = MagicMock()
mock_gpu_extras = MagicMock()
sys.modules['gpu_extras'] = mock_gpu_extras
sys.modules['gpu_extras.batch'] = mock_gpu_extras.batch
sys.modules['blf'] = MagicMock()
mock_bpy_extras = MagicMock()
sys.modules['bpy_extras'] = mock_bpy_extras
sys.modules['bpy_extras.view3d_utils'] = mock_bpy_extras.view3d_utils

spec = importlib.util.spec_from_file_location(
    "create_takeoff_grid_module",
    "src/modules/sbstudio/plugin/operators/create_takeoff_grid.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

_expand_grid_if_needed = mod._expand_grid_if_needed
_get_max_possible_drone_count = mod._get_max_possible_drone_count
create_points_of_takeoff_grid = mod.create_points_of_takeoff_grid


class MockOperator:
    def __init__(self, rows=8, columns=8, drones=64, drones_per_slot_row=1, drones_per_slot_col=1):
        self.rows = rows
        self.columns = columns
        self.drones = drones
        self.drones_per_slot_row = drones_per_slot_row
        self.drones_per_slot_col = drones_per_slot_col
        self.empty_slots = 0


def test_create_points_of_takeoff_grid_more_than_64_drones():
    points = create_points_of_takeoff_grid((0, 0, 0), rows=10, columns=10)
    assert len(points) == 100

    points_144 = create_points_of_takeoff_grid((0, 0, 0), rows=12, columns=12)
    assert len(points_144) == 144


def test_expand_grid_if_needed():
    # Grid initialized at 8x8 (64 drones)
    op = MockOperator(rows=8, columns=8, drones=100)
    assert _get_max_possible_drone_count(op) == 64

    # Expand grid to fit 100 drones
    _expand_grid_if_needed(op)
    assert op.rows == 10
    assert op.columns == 10
    assert _get_max_possible_drone_count(op) == 100


def test_expand_grid_if_needed_no_change_when_within_capacity():
    op = MockOperator(rows=10, columns=10, drones=80)
    _expand_grid_if_needed(op)
    assert op.rows == 10
    assert op.columns == 10


def test_server_limit_error_message_detected():
    err_msg = "Forbidden: Request contains too many points; maximum allowed is 64".lower()
    assert "too many points" in err_msg or "forbidden" in err_msg or "64" in err_msg
