from bpy.types import Operator

from sbstudio.plugin.model.led_control import (
    set_expected_3d_viewport_shader_configuration_of_context,
)
from sbstudio.plugin.views import redraw_all_3d_views

__all__ = ("FixViewportLEDShaderOperator",)


class FixViewportLEDShaderOperator(Operator):
    """Switches the 3D view so per-drone LED colors are visible."""

    bl_idname = "skybrush.fix_viewport_led_shader"
    bl_label = "Show LED Colors in Viewport"
    bl_description = (
        "Switches Solid view to Object Color so the LED colors already "
        "assigned to the drones become visible"
    )

    def execute(self, context):
        set_expected_3d_viewport_shader_configuration_of_context(context)
        try:
            redraw_all_3d_views()
        except Exception:
            pass
        return {"FINISHED"}
