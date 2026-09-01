"""Tests for the local .skyc writer."""

import sys
from base64 import b64decode
from pathlib import Path
from unittest.mock import MagicMock
from zipfile import ZipFile

# Blender-only dependency pulled in by model.color / model.point
sys.modules.setdefault("mathutils", MagicMock())

from sbstudio.model.color import Color4D
from sbstudio.model.light_program import LightProgram
from sbstudio.model.point import Point4D
from sbstudio.model.safety_check import SafetyCheckParams
from sbstudio.model.skyc import encode_light_program_bytecode, write_skyc
from sbstudio.model.time_markers import TimeMarkers
from sbstudio.model.trajectory import Trajectory


def test_light_bytecode_matches_studio_server_samples():
    cases = [
        (
            LightProgram([Color4D(0.0, 255, 0, 0, is_fade=False)]),
            5.0,
            bytes.fromhex("04ff0000fa0100"),
        ),
        (
            LightProgram(
                [
                    Color4D(0.0, 255, 0, 0, is_fade=False),
                    Color4D(1.0, 0, 255, 0, is_fade=False),
                ]
            ),
            5.0,
            bytes.fromhex("04ff0000000800ff003202c80100"),
        ),
        (
            LightProgram(
                [
                    Color4D(0.0, 255, 0, 0, is_fade=False),
                    Color4D(2.0, 0, 255, 0, is_fade=True),
                ]
            ),
            5.0,
            bytes.fromhex("04ff0000000800ff006402960100"),
        ),
    ]
    for program, end_time, expected in cases:
        assert encode_light_program_bytecode(program, end_time=end_time) == expected


def test_write_skyc_creates_valid_zip(tmp_path: Path):
    trajectories = {
        "Drone 1": Trajectory(
            [
                Point4D(0.0, 0.0, 0.0, 0.0),
                Point4D(2.0, 0.0, 0.0, 5.0),
                Point4D(4.0, 0.0, 0.0, 0.0),
            ]
        ),
        "Drone 2": Trajectory(
            [
                Point4D(0.0, 3.0, 0.0, 0.0),
                Point4D(2.0, 3.0, 0.0, 5.0),
                Point4D(4.0, 3.0, 0.0, 0.0),
            ]
        ),
    }
    lights = {
        "Drone 1": LightProgram(
            [
                Color4D(0.0, 255, 0, 0, is_fade=False),
                Color4D(2.0, 0, 255, 0, is_fade=True),
            ]
        ),
        "Drone 2": LightProgram([Color4D(0.0, 0, 0, 255, is_fade=False)]),
    }

    out = tmp_path / "show.skyc"
    write_skyc(
        out,
        trajectories=trajectories,
        lights=lights,
        validation=SafetyCheckParams(),
        time_markers=TimeMarkers(markers={"Takeoff": 0.0}),
        show_title="unit-test",
        show_type="outdoor",
        show_segments={"show": (0.0, 4.0)},
    )

    assert out.is_file()
    assert out.stat().st_size > 0

    with ZipFile(out) as archive:
        names = set(archive.namelist())
        assert "show.json" in names
        assert "cues.json" in names
        assert "drones/Drone 1/trajectory.json" in names
        assert "drones/Drone 1/lights.json" in names
        assert "drones/Drone 2/trajectory.json" in names
        assert "drones/Drone 2/lights.json" in names

        import json

        show = json.loads(archive.read("show.json"))
        assert show["version"] == 1
        assert show["meta"]["title"] == "unit-test"
        assert "id" in show["meta"]
        assert len(show["swarm"]["drones"]) == 2

        lights_json = json.loads(archive.read("drones/Drone 1/lights.json"))
        raw = b64decode(lights_json["data"])
        # Trajectory ends at t=4, so hold after the fade is 2s (not 3s).
        assert raw == bytes.fromhex("04ff0000000800ff0064026400")

        traj = json.loads(archive.read("drones/Drone 1/trajectory.json"))
        assert traj["takeoffTime"] == 0.0
        assert traj["landingTime"] == 4.0
        assert traj["version"] == 1


def test_trajectory_stays_version_1(tmp_path: Path):
    """The Skybrush player refuses any other version, so this must never change.

    ``Trajectory.as_dict`` defaults to the smaller binary version 2, which is
    only valid on the wire to Studio Server, not inside a .skyc file.
    """
    out = tmp_path / "v1.skyc"
    write_skyc(
        out,
        trajectories={"D": Trajectory([Point4D(0.0, 0.0, 0.0, 0.0)])},
        lights={"D": LightProgram([Color4D(0.0, 1, 2, 3)])},
    )
    with ZipFile(out) as archive:
        import json

        traj = json.loads(archive.read("drones/D/trajectory.json"))
        assert traj["version"] == 1
        assert isinstance(traj["points"], list)


def test_coordinates_are_rounded_but_timestamps_keep_millisecond_precision(
    tmp_path: Path,
):
    """Coordinates dominate the file size; timestamps must stay exact for sync."""
    trajectory = Trajectory(
        [
            Point4D(0.0, 0.0, 0.0, 0.0),
            Point4D(1.234, 1.23456, 2.98765, 3.5),
        ]
    )
    out = tmp_path / "rounded.skyc"
    write_skyc(out, trajectories={"D": trajectory}, pos_digits=2)

    with ZipFile(out) as archive:
        import json

        points = json.loads(archive.read("drones/D/trajectory.json"))["points"]

    assert points[1][0] == 1.234, "timestamp lost precision"
    assert points[1][1] == [1.23, 2.99, 3.5], points[1][1]


def test_writer_does_not_simplify_unless_asked(tmp_path: Path):
    """Simplifying again after the sampler would exceed the requested tolerance."""
    colors = [Color4D(i * 0.1, i, i, i) for i in range(40)]
    trajectory = Trajectory([Point4D(i * 0.1, i * 0.5, 0.0, 10.0) for i in range(40)])

    out = tmp_path / "asis.skyc"
    write_skyc(out, trajectories={"D": trajectory}, lights={"D": LightProgram(colors)})
    with ZipFile(out) as archive:
        import json

        points = json.loads(archive.read("drones/D/trajectory.json"))["points"]
    assert len(points) == 40, "writer silently simplified the trajectory"

    out2 = tmp_path / "simplified.skyc"
    write_skyc(
        out2,
        trajectories={"D": Trajectory(trajectory.points)},
        lights={"D": LightProgram(colors)},
        trajectory_eps=0.05,
    )
    with ZipFile(out2) as archive:
        import json

        points2 = json.loads(archive.read("drones/D/trajectory.json"))["points"]
    assert len(points2) < len(points), "explicit tolerance did not simplify"


def test_write_skyc_scales_to_100_drones(tmp_path: Path):
    trajectories = {
        f"D{i}": Trajectory(
            [Point4D(0.0, float(i), 0.0, 0.0), Point4D(1.0, float(i), 0.0, 5.0)]
        )
        for i in range(100)
    }
    lights = {
        name: LightProgram([Color4D(0.0, 255, 255, 255, is_fade=False)])
        for name in trajectories
    }
    out = tmp_path / "big.skyc"
    write_skyc(out, trajectories=trajectories, lights=lights, show_title="100")
    with ZipFile(out) as archive:
        assert (
            sum(1 for n in archive.namelist() if n.endswith("trajectory.json")) == 100
        )
