import logging
from math import ceil, inf
from typing import Sequence

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Context

from sbstudio.api import SkybrushStudioAPI
from sbstudio.api.types import Version
from sbstudio.api.version import is_backend_version_at_least
from sbstudio.errors import SkybrushStudioError
from sbstudio.math.decomposition import decompose_points_locally
from sbstudio.math.nearest_neighbors import find_nearest_neighbors
from sbstudio.model.types import Coordinate3D
from sbstudio.plugin.api import call_api_from_blender_operator, get_api
from sbstudio.plugin.constants import Collections, Formations
from sbstudio.plugin.model.formation import (
    create_formation,
    ensure_formation_consists_of_points,
)
from sbstudio.plugin.model.global_settings import get_preferences
from sbstudio.plugin.model.safety_check import get_proximity_warning_threshold
from sbstudio.plugin.model.storyboard import (
    Storyboard,
    StoryboardEntryPurpose,
    get_storyboard,
)
from sbstudio.plugin.operators.recalculate_transitions import (
    RecalculationTask,
    recalculate_transitions,
)
from sbstudio.plugin.utils.evaluator import create_position_evaluator

from .base import StoryboardOperator

__all__ = ("TakeoffOperator",)

#############################################################################
# configure logger

log = logging.getLogger(__name__)

_SMOOTH_PEAK_VELOCITY_FACTOR = 1.5
"""Ratio of the peak to the average velocity of a smooth transition.

The influence curve of a smooth transition is a cubic Bezier whose handles sit
one third of the way into the interval, which reduces to ``3t^2 - 2t^3``. Its
first derivative peaks at 1.5 halfway through the transition.
"""

_SMOOTH_PEAK_ACCELERATION_FACTOR = 6.0
"""Largest absolute value of the second derivative of ``3t^2 - 2t^3``, reached
at both ends of a smooth transition. Multiply by ``distance / duration ** 2``
to obtain the peak acceleration of the maneuver.
"""

_PROFILE_AFTER_TAKEOFF = {
    # A takeoff that ends at a standstill must be followed by a transition that
    # also departs from a standstill, and one that ends at full climb velocity
    # by a transition that picks that velocity up ("smooth from right" is smooth
    # at its right end only). Pairing them the other way around puts a step in
    # the velocity right where the takeoff ends.
    "SMOOTH": "SMOOTH",
    "LINEAR": "SMOOTH_FROM_RIGHT",
}
"""Velocity profile that the storyboard entry following the takeoff needs in
order to keep the velocity continuous, keyed by the profile of the takeoff.
"""


def use_custom_spacing_updated(self, context: Context):
    """Called when the use_custom_spacing checkbox is enabled or disabled by the user."""
    if not self.use_custom_spacing:
        self.spacing = get_proximity_warning_threshold(context)


class TakeoffOperator(StoryboardOperator):
    """Blender operator that adds a takeoff transition to the show, starting at
    a given frame.
    """

    bl_idname = "skybrush.takeoff"
    bl_label = "Takeoff"
    bl_description = "Add a takeoff maneuver to all the drones"
    bl_options = {"REGISTER", "UNDO"}

    only_with_valid_storyboard = True

    start_frame = IntProperty(
        name="at Frame", description="Start frame of the takeoff maneuver"
    )

    velocity = FloatProperty(
        name="with Velocity",
        description=(
            "Average vertical velocity during the takeoff maneuver. This is "
            "what sets the duration of the maneuver; with a smooth velocity "
            "profile the drones briefly climb faster than this in the middle "
            "of the maneuver"
        ),
        default=1.5,
        min=0.1,
        soft_min=0.1,
        soft_max=10,
        unit="VELOCITY",
    )

    velocity_profile = EnumProperty(
        name="Velocity Profile",
        description=(
            "Shape of the vertical velocity curve during the takeoff maneuver"
        ),
        items=[
            (
                "SMOOTH",
                "Smooth",
                (
                    "Ramp the climb velocity up from a standstill and back down "
                    "to a hover, keeping the acceleration bounded. Recommended"
                ),
                1,
            ),
            (
                "LINEAR",
                "Constant velocity",
                (
                    "Climb at a constant velocity, which means the drones reach "
                    "and leave that velocity instantaneously. Only safe when the "
                    "next transition starts exactly where the takeoff ends and "
                    "carries the same velocity onwards"
                ),
                2,
            ),
        ],
        default="SMOOTH",
    )

    altitude = FloatProperty(
        name="to Altitude",
        description=(
            "Altitude to take off to. In case of layered takeoff "
            "the desired takeoff altitude of the lowest layer"
        ),
        default=6,
        soft_min=0,
        soft_max=50,
        unit="LENGTH",
    )

    # TODO(ntamas): test whether it is safe to remove this property without
    # breaking compatibility with older versions

    altitude_is_relative = BoolProperty(
        name="Relative Altitude",
        description=(
            "Specifies whether the takeoff altitude is relative to the current "
            "altitude of the drone. Deprecated; not used any more."
        ),
        default=False,
        options={"HIDDEN"},
    )

    use_custom_spacing = BoolProperty(
        name="Use custom spacing",
        default=False,
        description=(
            "When checked, a custom spacing can be given instead of "
            "the default proximity warning threshold"
        ),
        update=use_custom_spacing_updated,
    )

    spacing = FloatProperty(
        name="Spacing",
        description="Minimum distance between drones during takeoff",
        default=3,
        min=0.1,
        soft_max=50,
        unit="LENGTH",
    )

    altitude_shift = FloatProperty(
        name="Layer height",
        description=(
            "Specifies the difference between altitudes of takeoff layers "
            "for multi-phase takeoffs when multiple drones occupy the same "
            "takeoff slot within safety distance."
        ),
        default=5,
        soft_min=0,
        soft_max=50,
        unit="LENGTH",
    )

    @classmethod
    def poll(cls, context: Context):
        if not super().poll(context):
            return False

        drones = Collections.find_drones(create=False)
        return drones is not None and len(drones.objects) > 0

    def draw(self, context: Context):
        layout = self.layout
        layout.use_property_split = True

        layout.prop(self, "start_frame")
        layout.prop(self, "velocity")
        layout.prop(self, "velocity_profile")
        layout.prop(self, "altitude")

        peak_velocity, peak_acceleration = self._estimate_peaks()
        col = layout.column(align=True)
        col.label(text=f"Peak climb velocity: {peak_velocity:.1f} m/s")
        if peak_acceleration is None:
            col.label(text="Peak acceleration: unbounded", icon="ERROR")
        else:
            settings = getattr(context.scene.skybrush, "settings", None)
            preferred = settings.max_acceleration if settings else 4
            row = col.row()
            if peak_acceleration > preferred:
                row.alert = True
            row.label(text=f"Peak acceleration: {peak_acceleration:.1f} m/s\u00b2")

        row = layout.row()
        row.prop(self, "altitude_shift")
        if self.altitude_shift < self.spacing:
            row.alert = True
            row.label(text="", icon="ERROR")
        row = layout.row(heading="Spacing")
        row.prop(self, "use_custom_spacing", text="")
        row = row.row()
        row.prop(self, "spacing", text="")
        row.enabled = self.use_custom_spacing
        if self.spacing < get_proximity_warning_threshold(context):
            row.alert = True
            row.label(text="", icon="ERROR")

    def invoke(self, context: Context, event):
        # The start frame cannot be earlier than the start time of the first
        # formation and must be earlier than the start time of the second
        # formation. Constrain it to the valid range.
        start, end = self._get_valid_range_for_start_frame(context)
        self.start_frame = int(max(min(context.scene.frame_current, end), start))

        if not self.use_custom_spacing:
            self.spacing = get_proximity_warning_threshold(context)

        return context.window_manager.invoke_props_dialog(self)

    def execute_on_storyboard(self, storyboard: Storyboard, entries, context: Context):
        try:
            success = self._run(storyboard, context=context)
        except SkybrushStudioError:
            # These are handled nicely
            success = False
        return {"FINISHED"} if success else {"CANCELLED"}

    def _run(self, storyboard: Storyboard, *, context: Context) -> bool:
        bpy.ops.skybrush.prepare()

        if not self._validate_start_frame(context):
            return False

        drones = Collections.find_drones().objects
        if not drones:
            return False

        source, target, _ = create_helper_formation_for_takeoff_and_landing(
            drones,
            frame=self.start_frame,
            base_altitude=self.altitude,
            layer_height=self.altitude_shift,
            min_distance=self.spacing,
            operator=self,
        )

        # Calculate the Z distance to travel for each drone
        diffs = [t[2] - s[2] for s, t in zip(source, target)]
        if min(diffs) < 0:
            dist = abs(min(diffs))
            self.report(
                {"ERROR"},
                f"At least one drone would have to take off downwards by {dist}m",
            )
            return False

        # Calculate takeoff durations from distances to travel and the
        # average velocity
        fps = context.scene.render.fps
        takeoff_durations = [int(ceil((diff / self.velocity) * fps)) for diff in diffs]

        # We ensure that drones arrive at the same time, so calculate the
        # takeoff delays for those drones that take off to lower altitudes
        takeoff_duration = max(takeoff_durations)
        delays = [takeoff_duration - d for d in takeoff_durations]

        # Calculate when the takeoff should end
        end_of_takeoff = self.start_frame + takeoff_duration
        if len(storyboard.entries) > 1:
            assert storyboard.second_entry is not None
            first_frame = storyboard.second_entry.frame_start
            if first_frame < end_of_takeoff:
                self.report(
                    {"ERROR"},
                    f"Takeoff maneuver needs at least {takeoff_duration} frames; "
                    f"there is not enough time after the first entry of the "
                    f"storyboard (frame {first_frame})",
                )
                return False

        # If there are no storyboard entries yet, add a new entry with the
        # sources of the takeoff. If there is at least one entry, ensure that
        # the markers in that entry are at the positions that we designed the
        # takeoff from. (This may be necessary if the user picks a frame
        # between the first and the second formation and there are keyframes
        # or other mechanisms that move the drones between the two.
        entry = storyboard.first_entry
        if entry is None:
            entry = storyboard.add_new_entry(
                formation=create_formation(Formations.TAKEOFF_GRID, source),
                frame_start=self.start_frame,
                duration=0,
                purpose=StoryboardEntryPurpose.TAKEOFF,
                select=False,
                context=context,
            )
        else:
            formation = entry.formation
            if formation is None:
                self.report(
                    {"ERROR"},
                    "First storyboard entry must have an associated formation",
                )
            ensure_formation_consists_of_points(formation, source)

        # Add or update the storyboard entry with the targets of the takeoff.
        # `purpose` is a Blender enum property, so it reads back as the name of
        # the enum member rather than the member itself.
        second_entry = storyboard.second_entry if len(storyboard.entries) > 1 else None
        if (
            second_entry is not None
            and second_entry.purpose == StoryboardEntryPurpose.TAKEOFF.name
        ):
            entry = second_entry
            entry.frame_start = end_of_takeoff
            if entry.formation:
                ensure_formation_consists_of_points(entry.formation, target)
            else:
                entry.formation = create_formation(Formations.TAKEOFF, target)
        else:
            entry = storyboard.add_new_entry(
                formation=create_formation(Formations.TAKEOFF, target),
                frame_start=end_of_takeoff,
                duration=0,
                purpose=StoryboardEntryPurpose.TAKEOFF,
                select=True,
                context=context,
            )
        assert entry is not None
        entry.transition_type = "MANUAL"
        entry.transition_velocity_profile = self.velocity_profile
        self._match_profile_of_entry_after_takeoff(storyboard)

        # Set up the custom departure delays for the drones
        if delays and max(delays) > 0:
            entry.schedule_overrides_enabled = True
            for index, delay in enumerate(delays):
                if delay > 0:
                    override = entry.add_new_schedule_override()
                    override.index = index
                    override.pre_delay = delay

        # Recalculate the transitions leading from and to the target formation
        # as well as the constraint holding the drones at the takeoff grid
        tasks = [
            RecalculationTask.for_entry_by_index(storyboard.entries, 0),
            RecalculationTask.for_entry_by_index(storyboard.entries, 1),
        ]
        if len(storyboard.entries) > 2:
            tasks.append(RecalculationTask.for_entry_by_index(storyboard.entries, 2))

        start_of_scene = min(context.scene.frame_start, storyboard.frame_start)
        if get_preferences().plan_transitions_locally:
            recalculate_transitions(tasks, start_of_scene=start_of_scene)
        else:
            try:
                with call_api_from_blender_operator(self, "transition planner"):
                    recalculate_transitions(tasks, start_of_scene=start_of_scene)
            except Exception as ex:
                log.info(
                    f"Server connection failed ({ex}); calculating transitions locally."
                )
                recalculate_transitions(tasks, start_of_scene=start_of_scene)

        return True

    def _estimate_peaks(self) -> tuple[float, float | None]:
        """Estimates the peak vertical velocity and acceleration of the takeoff.

        Returns:
            the peak climb velocity in m/s, and the peak acceleration in m/s^2.
            The latter is ``None`` for the constant-velocity profile, where the
            drones start and stop climbing within a single frame and the
            acceleration is therefore limited only by the frame rate.
        """
        if self.velocity_profile == "LINEAR":
            return self.velocity, None

        # Drones assigned to a higher takeoff layer climb further, but they are
        # given proportionally more time so that everyone arrives together, so
        # they accelerate more gently. The lowest layer, which climbs by
        # `altitude`, is therefore the worst case.
        distance = max(self.altitude, 1e-3)
        duration = distance / self.velocity
        return (
            _SMOOTH_PEAK_VELOCITY_FACTOR * self.velocity,
            _SMOOTH_PEAK_ACCELERATION_FACTOR * distance / (duration * duration),
        )

    def _match_profile_of_entry_after_takeoff(self, storyboard: Storyboard) -> None:
        """Updates the velocity profile of the storyboard entry that follows the
        takeoff so that the velocity stays continuous where the two meet.

        Only the profile that used to pair with the other takeoff profile is
        replaced, so a profile that the user picked deliberately is left alone.
        """
        entries = storyboard.entries
        if len(entries) <= 2:
            return

        wanted = _PROFILE_AFTER_TAKEOFF[self.velocity_profile]
        stale = {
            profile
            for takeoff_profile, profile in _PROFILE_AFTER_TAKEOFF.items()
            if takeoff_profile != self.velocity_profile
        }

        entry = entries[2]
        if entry.transition_velocity_profile in stale:
            entry.transition_velocity_profile = wanted

    def _get_valid_range_for_start_frame(self, context: Context) -> tuple[float, float]:
        """Returns the interval that must contain the start frame of the takeoff
        operation.

        The returned range is closed from the left and open from the right.
        """
        # Note: we assume here that the first entry is the takeoff grid on ground
        storyboard = get_storyboard(context=context)
        if len(storyboard.entries) <= 0:
            # Storyboard is empty
            return -inf, inf
        elif len(storyboard.entries) == 1:
            # Storyboard has a takeoff grid only
            assert storyboard.first_entry is not None
            return storyboard.first_entry.frame_end, inf
        else:
            # Storyboard has both a takeoff grid and an existing takeoff
            # formation
            assert storyboard.first_entry is not None
            assert storyboard.second_entry is not None
            return storyboard.first_entry.frame_end, storyboard.second_entry.frame_start

    def _validate_start_frame(self, context: Context) -> bool:
        """Returns whether the takeoff time chosen by the user is valid."""
        start, end = self._get_valid_range_for_start_frame(context)
        if self.start_frame < start:
            self.report(
                {"ERROR"},
                (
                    f"Takeoff maneuver must start after the first (takeoff "
                    f"grid) entry of the storyboard (frame {start})"
                ),
            )
            return False

        if self.start_frame >= end:
            self.report(
                {"ERROR"},
                (
                    f"Takeoff maneuver must start before the second "
                    f"entry of the storyboard (frame {end})"
                ),
            )
            return False

        return True


def create_helper_formation_for_takeoff_and_landing(
    drones,
    *,
    frame: int,
    base_altitude: float,
    layer_height: float,
    min_distance: float,
    flatten_source: bool = False,
    operator=None,
):
    """Creates a layer helper formation for takeoff and landing where the drones
    are placed directly above their positions at the given frame, at the given
    base altitude plus an altitude shift per layer to ensure minimum distance
    constraints.

    Returns:
        the source points, the target points, and the assignment of target
        points to layers (layer 0 being at the lowest altitude)
    """
    # Evaluate the initial positions of the drones
    with create_position_evaluator() as get_positions_of:
        source = get_positions_of(drones, frame=frame)
    # Flatten source if needed (e.g if called from a show
    # starting with a floating grid and not from ground)
    if flatten_source:
        min_alt = min(p[2] for p in source)
        source = [(p[0], p[1], min_alt) for p in source]

    # Figure out how many phases we will need, based on the current safety
    # threshold and the arrangement of the drones
    _, _, dist = find_nearest_neighbors(source)
    if dist < min_distance:
        if get_preferences().plan_transitions_locally:
            # Decompose into layers here in Blender. No server is involved, so
            # the drone count limit of the community server does not apply.
            groups = decompose_points_locally(source, min_distance=min_distance)
        elif operator is not None:
            try:
                with call_api_from_blender_operator(operator, "takeoff planner") as api:
                    groups = plan_takeoff_with_api(source, min_distance, api)
            except Exception as ex:
                log.info(
                    f"Server takeoff planner unavailable or failed ({ex}); "
                    "falling back to local decomposition."
                )
                groups = decompose_points_locally(source, min_distance=min_distance)
        else:
            try:
                groups = plan_takeoff_with_api(source, min_distance, get_api())
            except Exception as ex:
                log.info(
                    f"Server takeoff planner unavailable or failed ({ex}); "
                    "falling back to local decomposition."
                )
                groups = decompose_points_locally(source, min_distance=min_distance)
    else:
        # We can save an API call here
        groups = [0] * len(source)

    num_groups = max(groups) + 1 if groups else 0

    # Prepare the points of the target formation to take off to or to return to
    target = [
        (x, y, base_altitude + (num_groups - group - 1) * layer_height)
        for (x, y, _), group in zip(source, groups)
    ]

    return source, target, groups


def plan_takeoff_with_api(
    source: Sequence[Coordinate3D], min_distance: float, api: SkybrushStudioAPI
) -> list[int]:
    if is_backend_version_at_least(Version(2, 43, 0), api=api):
        return api.plan_takeoff(source, min_distance=min_distance)
    else:
        groups = api.decompose_points(
            source, min_distance=min_distance, method="greedy"
        )
        if any(groups):
            log.warning(
                "Update your Studio Server to the latest version to plan takeoff with downwash minimization between layers"
            )
        return groups
