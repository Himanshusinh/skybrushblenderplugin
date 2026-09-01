"""Local writer for Skybrush Compiled (``.skyc``) show files.

A ``.skyc`` file is a ZIP archive of JSON files. Building it here lets Blender
export shows of any size without the community Studio Server's 64-drone limit.
"""

from __future__ import annotations

import json
import re
from base64 import b64encode
from hashlib import md5
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from natsort import natsorted

from .light_program import LightProgram
from .location import ShowLocation
from .safety_check import SafetyCheckParams
from .time_markers import TimeMarkers
from .trajectory import Trajectory

__all__ = ("write_skyc",)

_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _encode_varuint(value: int) -> bytes:
    """Encodes a non-negative integer as an unsigned LEB128 varuint."""
    if value < 0:
        raise ValueError("varuint cannot encode a negative value")

    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def encode_light_program_bytecode(
    light_program: LightProgram, *, end_time: float
) -> bytes:
    """Encodes a light program as pyledctrl bytecode (the form stored in ``.skyc``).

    Every keypoint of the program becomes a ``FADE_TO_COLOR`` spanning the gap to
    the previous one, so the player reproduces the same piecewise-linear curve
    that Blender shows. Keypoint reduction belongs to
    :meth:`LightProgram.simplify`, which bounds the error it introduces; doing it
    here as well would discard detail with no error bound.
    """
    colors = list(light_program.colors)
    if not colors:
        return bytes([0x00])  # END

    def _rgb(color) -> tuple[int, int, int]:
        return (
            max(0, min(255, int(color.r))),
            max(0, min(255, int(color.g))),
            max(0, min(255, int(color.b))),
        )

    # Durations are deltas between absolute tick positions rather than each gap
    # rounded on its own, otherwise the rounding error accumulates and the light
    # program drifts out of sync with the trajectory over a long show. The clock
    # starts at t=0 rather than at the first keypoint, so a program that begins
    # later than the trajectory does not get played early.
    def _ticks(t: float) -> int:
        return max(0, round(t * 50))

    if len(colors) == 1:
        buf = bytearray()
        buf.append(0x04)  # SET_COLOR
        buf.extend(_rgb(colors[0]))
        buf.extend(_encode_varuint(_ticks(end_time)))
        buf.append(0x00)  # END
        return bytes(buf)

    buf = bytearray()
    last_rgb = _rgb(colors[0])
    buf.append(0x04)  # SET_COLOR
    buf.extend(last_rgb)
    buf.extend(_encode_varuint(_ticks(colors[0].t)))

    previous_ticks = _ticks(colors[0].t)
    for current in colors[1:]:
        current_ticks = _ticks(current.t)
        duration = max(0, current_ticks - previous_ticks)
        previous_ticks = current_ticks

        cur_rgb = _rgb(current)
        if cur_rgb == last_rgb:
            if duration > 0:
                buf.append(0x02)  # SLEEP
                buf.extend(_encode_varuint(duration))
        else:
            buf.append(0x08)  # FADE_TO_COLOR
            buf.extend(cur_rgb)
            buf.extend(_encode_varuint(duration))
            last_rgb = cur_rgb

    hold = max(0, round((end_time - colors[-1].t) * 50))
    if hold:
        buf.append(0x02)  # SLEEP
        buf.extend(_encode_varuint(hold))

    buf.append(0x00)  # END
    return bytes(buf)


def _safe_drone_dirname(name: str) -> str:
    cleaned = _INVALID_PATH_CHARS.sub("_", name).strip(" .")
    return cleaned or "drone"


def _trajectory_payload(
    trajectory: Trajectory,
    *,
    ndigits: int = 3,
    pos_digits: int = 2,
    eps: float | None = None,
) -> dict[str, Any]:
    """Builds the version 1 trajectory payload.

    Timestamps keep millisecond precision so that the show stays in sync, while
    coordinates are rounded more coarsely: they dominate the file size and the
    player cannot fly more accurately than that anyway. Only version 1 is
    produced on purpose — the Skybrush player rejects every other version.
    """
    if eps is not None:
        trajectory = trajectory.simplify_in_place(eps=eps)

    payload: dict[str, Any] = {
        "points": [
            [
                round(point.t, ndigits),
                [
                    round(point.x, pos_digits),
                    round(point.y, pos_digits),
                    round(point.z, pos_digits),
                ],
                [],
            ]
            for point in trajectory.points
        ],
        "version": 1,
    }

    first = trajectory.first_point
    last = trajectory.last_point
    payload["takeoffTime"] = round(first.t, ndigits) if first else 0.0
    payload["landingTime"] = round(last.t, ndigits) if last else 0.0
    return payload


def _point_xyz(point, *, ndigits: int = 3) -> list[float]:
    return [
        round(point.x, ndigits),
        round(point.y, ndigits),
        round(point.z, ndigits),
    ]


def write_skyc(
    filepath: str | Path,
    *,
    trajectories: dict[str, Trajectory],
    lights: dict[str, LightProgram] | None = None,
    validation: SafetyCheckParams | None = None,
    time_markers: TimeMarkers | None = None,
    show_title: str | None = None,
    show_type: str = "outdoor",
    show_location: ShowLocation | None = None,
    show_segments: dict[str, tuple[float, float]] | None = None,
    ndigits: int = 3,
    pos_digits: int = 2,
    light_eps: float | None = None,
    trajectory_eps: float | None = None,
) -> Path:
    """Writes a Skybrush Compiled (``.skyc``) show file.

    Parameters:
        filepath: destination path (``\".skyc\"`` extension recommended)
        trajectories: per-drone trajectories keyed by drone name
        lights: per-drone light programs keyed by drone name
        validation: safety-check thresholds embedded in the show
        time_markers: optional cue markers
        show_title: optional title stored in show metadata
        show_type: ``\"outdoor\"`` or ``\"indoor\"``
        show_location: optional geodetic show origin
        show_segments: optional named time ranges
        ndigits: rounding precision of timestamps
        pos_digits: rounding precision of coordinates, in decimal digits of a
            metre; 2 means centimetres
        light_eps: maximum per-channel colour error (0-255) allowed when
            reducing the number of light keypoints, or ``None`` to write the
            programs as they are
        trajectory_eps: maximum positional error in metres allowed when
            reducing the number of trajectory points, or ``None`` to write the
            trajectories as they are

    Callers that have already simplified their input should leave ``light_eps``
    and ``trajectory_eps`` at ``None``: simplifying twice compounds the error
    beyond the requested tolerance.

    Returns:
        the path that was written
    """
    if not trajectories:
        raise ValueError("Cannot write an empty .skyc file (no drones)")

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    lights = lights or {}
    validation = validation or SafetyCheckParams()
    time_markers = time_markers or TimeMarkers()

    cues = time_markers.as_dict(ndigits=ndigits)
    files: dict[str, bytes] = {
        "cues.json": json.dumps(cues, indent=2).encode("utf-8"),
    }

    drone_entries: list[dict[str, Any]] = []
    used_dirs: set[str] = set()

    for name in natsorted(trajectories.keys()):
        trajectory = trajectories[name]
        light_program = lights.get(name) or LightProgram()
        if light_eps is not None:
            light_program = light_program.simplify(eps=light_eps)

        dirname = _safe_drone_dirname(name)
        base = dirname
        suffix = 2
        while dirname in used_dirs:
            dirname = f"{base}_{suffix}"
            suffix += 1
        used_dirs.add(dirname)

        traj_rel = f"./drones/{dirname}/trajectory.json#"
        lights_rel = f"./drones/{dirname}/lights.json#"

        end_time = float(trajectory.last_time or 0.0)
        light_bytes = encode_light_program_bytecode(
            light_program, end_time=end_time
        )

        traj_json = _trajectory_payload(
            trajectory, ndigits=ndigits, pos_digits=pos_digits, eps=trajectory_eps
        )
        lights_json = {
            "version": 1,
            "data": b64encode(light_bytes).decode("ascii"),
        }

        files[f"drones/{dirname}/trajectory.json"] = json.dumps(
            traj_json, separators=(",", ":")
        ).encode("utf-8")
        files[f"drones/{dirname}/lights.json"] = json.dumps(
            lights_json, separators=(",", ":")
        ).encode("utf-8")

        first = trajectory.first_point
        last = trajectory.last_point
        settings: dict[str, Any] = {
            "trajectory": {"$ref": traj_rel},
            "lights": {"$ref": lights_rel},
            "home": _point_xyz(first, ndigits=pos_digits) if first else [0.0, 0.0, 0.0],
            "landAt": _point_xyz(last, ndigits=pos_digits) if last else [0.0, 0.0, 0.0],
            "name": name,
        }
        drone_entries.append({"type": "generic", "settings": settings})

    environment: dict[str, Any] = {"type": show_type}
    if show_location is not None:
        environment["location"] = show_location.json

    meta: dict[str, Any] = {}
    if show_title:
        meta["title"] = show_title
    if show_segments:
        meta["segments"] = {
            key: [round(start, ndigits), round(end, ndigits)]
            for key, (start, end) in show_segments.items()
        }

    show: dict[str, Any] = {
        "version": 1,
        "settings": {
            "cues": {"$ref": "./cues.json"},
            "validation": validation.as_dict(ndigits=ndigits),
        },
        "swarm": {"drones": drone_entries},
        "environment": environment,
        "meta": meta,
        "media": {},
    }

    # Stable content hash used as show id (same role as the server-generated id).
    canonical = json.dumps(show, sort_keys=True, separators=(",", ":")).encode("utf-8")
    meta["id"] = md5(canonical).hexdigest()

    files["show.json"] = json.dumps(show, indent=2).encode("utf-8")

    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with ZipFile(tmp_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            # show.json first, then cues, then drones — matches Studio Server order
            for name in ("show.json", "cues.json"):
                archive.writestr(name, files[name])
            for name in sorted(n for n in files if n.startswith("drones/")):
                archive.writestr(name, files[name])
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

    return path
