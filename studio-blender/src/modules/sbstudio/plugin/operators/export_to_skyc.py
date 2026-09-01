from typing import Any

from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty

from sbstudio.model.file_formats import FileFormat

from .base import ExportOperator

__all__ = ("SkybrushExportOperator",)


#############################################################################
# Operator that allows the user to invoke the .skyc export operation
#############################################################################


class SkybrushExportOperator(ExportOperator):
    """Export object trajectories and light animation into the Skybrush compiled format (.skyc)"""

    bl_idname = "export_scene.skybrush"
    bl_label = "Export Skybrush SKYC"
    bl_options = {"REGISTER"}

    # List of file extensions that correspond to Skybrush files
    filter_glob = StringProperty(default="*.skyc", options={"HIDDEN"})
    filename_ext = ".skyc"

    # output trajectory frame rate
    output_fps = IntProperty(
        name="Trajectory FPS",
        default=4,
        description="Number of samples to take from trajectories per second",
    )

    # output light program frame rate
    light_output_fps = IntProperty(
        name="Light FPS",
        default=25,
        description=(
            "Number of samples to take from light programs per second. Raise "
            "this if fast flashes or strobes look coarse in the exported show. "
            "Values that divide 50 (5, 10, 25, 50) land exactly on the timing "
            "grid of the file format and avoid rounding the keypoints in time"
        ),
    )

    # allowed colour error when reducing the number of light keypoints
    light_eps = IntProperty(
        name="Light color tolerance",
        default=4,
        min=0,
        max=64,
        description=(
            "Largest color error (0-255 per channel) allowed when compressing "
            "light programs. Lower values give smoother fades and bigger files; "
            "values above 8 cause visible banding"
        ),
    )

    # allowed positional error when reducing the number of trajectory points
    trajectory_eps = FloatProperty(
        name="Trajectory tolerance",
        default=0.05,
        min=0.0,
        max=1.0,
        step=1,
        precision=3,
        unit="LENGTH",
        description=(
            "Largest positional error allowed when compressing trajectories. "
            "This is the main lever on file size. 5 cm keeps formations exact; "
            "20 cm is noticeably smaller but loosens tight formations"
        ),
    )

    # coordinate precision written into the file
    pos_digits = IntProperty(
        name="Coordinate decimals",
        default=2,
        min=1,
        max=3,
        description=(
            "Number of decimal places used for coordinates. 2 means centimetre "
            "precision and is around 8% smaller than millimetres for no visible "
            "difference"
        ),
    )

    # pyro control enable/disable
    use_pyro_control = BoolProperty(
        name="Export pyro (PRO)",
        description="Specifies whether the pyro program of each drone should be included in the show",
        default=False,
    )

    # yaw control enable/disable
    use_yaw_control = BoolProperty(
        name="Export yaw (PRO)",
        description="Specifies whether the yaw angle of each drone should be controlled during the show",
        default=False,
    )

    # audio export enable/disable
    export_audio = BoolProperty(
        name="Export audio",
        description="Specifies whether a single audio file in the VSE should be exported into the show file",
        default=False,
    )

    # camera export enable/disable
    export_cameras = BoolProperty(
        name="Export cameras",
        description="Specifies whether cameras defined in Blender should be exported into the show file",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        layout.prop(self, "export_selected")
        layout.prop(self, "frame_range")
        layout.prop(self, "redraw")
        layout.prop(self, "output_fps")
        layout.prop(self, "light_output_fps")

        layout.separator()

        column = layout.column(align=True)
        column.label(text="File size vs. accuracy")
        column.prop(self, "trajectory_eps")
        column.prop(self, "light_eps")
        column.prop(self, "pos_digits")

        layout.separator()

        column = layout.column(align=True)
        column.prop(self, "export_audio")
        column.prop(self, "export_cameras")
        column.prop(self, "use_pyro_control")
        column.prop(self, "use_yaw_control")

    def get_format(self) -> FileFormat:
        return FileFormat.SKYC

    def get_operator_name(self) -> str:
        return ".skyc exporter"

    def get_settings(self) -> dict[str, Any]:
        return {
            "output_fps": self.output_fps,
            "light_output_fps": self.light_output_fps,
            "light_eps": self.light_eps,
            "trajectory_eps": self.trajectory_eps,
            "pos_digits": self.pos_digits,
            "use_pyro_control": self.use_pyro_control,
            "use_yaw_control": self.use_yaw_control,
            "export_audio": self.export_audio,
            "export_cameras": self.export_cameras,
        }
