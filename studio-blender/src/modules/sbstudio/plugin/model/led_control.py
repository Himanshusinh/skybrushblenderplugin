from collections.abc import Sequence
from typing import Literal, TypeAlias, overload

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, IntProperty
from bpy.types import Context, PropertyGroup

from sbstudio.plugin.callbacks import final_color_updated_callbacks
from sbstudio.plugin.colors import set_colors_of_drones_fast
from sbstudio.plugin.model.light_effects import LightEffectUpdate
from sbstudio.plugin.overlays.leds import (
    LEDsOverlay,
    LEDsOverlayMarker,
)
from sbstudio.plugin.props import ColorProperty
from sbstudio.plugin.views import find_all_3d_views

__all__ = (
    "LEDControlPanelProperties",
    "get_expected_3d_viewport_shader_configuration",
    "get_expected_3d_viewport_shader_configuration_from_context",
    "set_expected_3d_viewport_shader_configuration_of_context",
)


_overlay = None
"""Global LEDs marker overlay. This cannot be an attribute of LEDControlPanelProperties
for some reason; Blender PropertyGroup objects are weird.
"""


def _visualization_callback_for_markers(update: LightEffectUpdate) -> None:
    led_control = bpy.context.scene.skybrush.led_control
    positions, colors = update.get_positions_and_colors()
    overlay_markers: Sequence[LEDsOverlayMarker] = list(
        zip(positions, colors, strict=True)
    )
    led_control.update_overlay_markers(overlay_markers)


def _visualization_callback_for_materials(update: LightEffectUpdate) -> None:
    # Always push colors onto the drones. Skipping this when no light effect
    # is active left keyframed LED colors stuck behind a viewport that does
    # not read object.color (and export then sampled white from an empty cache).
    drones, colors = update.get_drones_and_colors()
    if not len(drones):
        return

    set_colors_of_drones_fast(drones, colors.ravel())
    for drone in drones:
        drone.update_tag()


ViewportShaderConfig: TypeAlias = tuple[
    Literal["THEME", "OBJECT"] | None, Literal["SINGLE", "OBJECT"] | None
]


def get_expected_3d_viewport_shader_configuration(
    visualization: str,
) -> ViewportShaderConfig:
    """Returns the expected wireframe and object color type to use in the 3D viewport
    shading settings for a given visualization mode.

    Args:
        visualization: the visualization mode

    Returns:
        A tuple of (expected_wireframe_color_type, expected_color_type) where each
        element is either a string representing the expected color type or None if
        there is no expectation for that color type.
    """
    match visualization:
        case "MATERIALS":
            return "OBJECT", "OBJECT"
        case "MARKERS":
            return "THEME", "SINGLE"
        case _:
            return None, None


def get_expected_3d_viewport_shader_configuration_from_context(
    context: Context,
) -> ViewportShaderConfig:
    """Returns the expected wireframe and object color type to use in the 3D viewport
    shading settings based on the current Blender context.

    Args:
        context: the current Blender context

    Returns:
        A tuple of (expected_wireframe_color_type, expected_color_type) where each
        element is either a string representing the expected color type or None if
        there is no expectation for that color type.
    """
    led_control = context.scene.skybrush.led_control
    if led_control is None:
        return None, None
    else:
        return get_expected_3d_viewport_shader_configuration(led_control.visualization)


@overload
def get_overlay() -> LEDsOverlay: ...


@overload
def get_overlay(create: bool) -> LEDsOverlay | None: ...


def get_overlay(create: bool = True):
    global _overlay

    if _overlay is None and create:
        _overlay = LEDsOverlay()

    return _overlay


def _apply_led_shader_configuration(
    shading,
    expected_wireframe_color_type: str | None,
    expected_color_type: str | None,
) -> None:
    """Writes the LED color shading settings onto a single 3D view."""
    match shading.type:
        case "WIREFRAME":
            if expected_wireframe_color_type is not None:
                shading.wireframe_color_type = expected_wireframe_color_type
        case "SOLID":
            if expected_color_type is not None:
                shading.color_type = expected_color_type


def set_expected_3d_viewport_shader_configuration_of_context(context: Context) -> None:
    """Updates the 3D viewport shading settings based on the current Blender
    context to use the expected wireframe and object color type.

    Args:
        context: the current Blender context
    """
    expected_wireframe_color_type, expected_color_type = (
        get_expected_3d_viewport_shader_configuration_from_context(context)
    )
    if expected_wireframe_color_type is None and expected_color_type is None:
        return

    seen: set[int] = set()

    def apply_space(space) -> None:
        key = id(space)
        if key in seen:
            return
        seen.add(key)
        _apply_led_shader_configuration(
            space.shading, expected_wireframe_color_type, expected_color_type
        )

    for space in find_all_3d_views():
        apply_space(space)

    # load_post and extra workspaces are not always on the current screen.
    if context.window_manager is not None:
        for window in context.window_manager.windows:
            screen = window.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        apply_space(space)


def visualization_updated(
    self: "LEDControlPanelProperties", context: Context | None = None
):
    # unregister
    if self.visualization != "MARKERS":
        if final_color_updated_callbacks.ensure_missing(
            _visualization_callback_for_markers
        ):
            self.clear_overlay_markers()

    if self.visualization != "MATERIALS":
        if final_color_updated_callbacks.ensure_missing(
            _visualization_callback_for_materials
        ):
            pass

    # register
    match self.visualization:
        case "MARKERS":
            final_color_updated_callbacks.ensure(_visualization_callback_for_markers)
        case "MATERIALS":
            final_color_updated_callbacks.ensure(_visualization_callback_for_materials)

    # update viewport shading according to selection
    if context is not None:
        set_expected_3d_viewport_shader_configuration_of_context(context)


class LEDControlPanelProperties(PropertyGroup):
    visualization = EnumProperty(
        items=[
            ("NONE", "None", "No rendering is very quick but invisible", 1),
            ("MARKERS", "Markers", "Markers are simple but quick", 2),
            ("MATERIALS", "Materials", "Material coloring looks nice but is slower", 3),
        ],
        name="Visualization",
        description=("The visualization method of the drone colors."),
        default="MATERIALS",
        update=visualization_updated,
    )
    """Visualization method of the drone colors."""

    marker_size = IntProperty(
        name="Marker size",
        description="Size of overlay markers for the MARKERS visualization type",
        min=1,
        soft_max=50,
        default=25,
    )

    primary_color = ColorProperty(
        name="Primary color", description="Primary color to set on the selected drones"
    )
    secondary_color = ColorProperty(
        name="Secondary color",
        description="Secondary color to set on the selected drones; used for gradients only",
        default=(0, 0, 0),
    )

    def clear_overlay_markers(self) -> None:
        """Clears the light effect overlay markers."""
        self.ensure_overlays_enabled_if_needed()

        overlay = get_overlay(create=False)
        if overlay:
            overlay.markers = []

    def ensure_overlays_enabled_if_needed(self) -> None:
        get_overlay().enabled = self.visualization == "MARKERS"

    def update_overlay_markers(self, markers: Sequence[LEDsOverlayMarker]) -> None:
        """Updates the light effect overlay markers."""
        self.ensure_overlays_enabled_if_needed()

        overlay = get_overlay(create=False)
        if overlay:
            overlay.markers = markers

    def swap_colors(self):
        primary, secondary = self.primary_color.copy(), self.secondary_color.copy()
        self.primary_color, self.secondary_color = secondary, primary


@persistent
def _on_load_initialize_callbacks(*args):
    scene = getattr(bpy.context, "scene", None)
    if (
        scene is not None
        and hasattr(scene, "skybrush")
        and hasattr(scene.skybrush, "led_control")
    ):
        visualization_updated(scene.skybrush.led_control, bpy.context)


def register():
    """Registers LED control subsystem."""
    if _on_load_initialize_callbacks not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_initialize_callbacks)
    _on_load_initialize_callbacks()


def unregister():
    """Unregisters LED control subsystem."""
    if _on_load_initialize_callbacks in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_initialize_callbacks)

    final_color_updated_callbacks.ensure_missing(_visualization_callback_for_markers)
    final_color_updated_callbacks.ensure_missing(_visualization_callback_for_materials)
