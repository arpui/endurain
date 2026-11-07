"""Build a Garmin COURSE .fit from a designed route and its honest annotations.

The watch navigates one geometry: the route polyline becomes RecordMessages,
and every warning rides as a CoursePointMessage anchored to the same instant
and distance as the record vertex nearest its ground position (the pairing
"Up Ahead" reads). A designed route has no clock, so the record timestamps
are synthesized at an even pace along the ground: they are pairing keys, not
claims about time - a course never asserts how long anything takes.

What may annotate a route is decided by truth, not by availability:
- named segments it runs along (the athlete's own corridors), always;
- technical warnings from the recorded track it was saved from, when it was
  saved from one (marks respected: denied stretches warn nobody, an
  overstated one reads TEC instead of DUR);
- technical warnings carried by the claimed ground under a drawn polyline
  (cells the athlete's own detector called hard), never for unknown ground;
- reconnection variants that are confirmed and not judged ``falsa``;
- the borders of ground the athlete has never crossed, marked to be
  confirmed rather than claimed.

Names stay under 16 bytes because the watch truncates them.
"""

import math
from datetime import UTC, datetime, timedelta

from fit_tool.fit_file import FitFile
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.course_message import CourseMessage
from fit_tool.profile.messages.course_point_message import CoursePointMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.profile_type import CoursePoint, Event, EventType, FileType, Manufacturer

EARTH_R_M = 6371000.0
#: synthesised pace for record timestamps: an even 2 m/s along the ground; the
#: values are pairing keys between records and course points, nothing more.
_SYNTH_PACE_MS = 2.0
_FIT_EPOCH_ZERO = datetime(1989, 12, 31, tzinfo=UTC)


def epoch_millis(stamp: datetime) -> int:
    """Absolute instant as unix-epoch milliseconds (fit-tool 0.9 convention)."""
    return round(stamp.timestamp() * 1000)


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Ground distance between two ``(lat, lng)`` points, metres."""
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    d_phi = math.radians(b[0] - a[0])
    d_lambda = math.radians(b[1] - a[1])
    h = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_R_M * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def cumulative(geo: list[tuple[float, float]]) -> list[float]:
    """Cumulative ground distance at each vertex."""
    cum = [0.0]
    for index in range(1, len(geo)):
        cum.append(cum[-1] + haversine_m(geo[index - 1], geo[index]))
    return cum


def clip_text(name: str) -> str:
    """Clip a point/course name to the 15 bytes the watch can show."""
    return name.encode("utf-8")[:15].decode("utf-8", errors="ignore")


def build_course_bytes(
    course_name: str,
    geo: list[tuple[float, float]],
    altitudes: list[float | None],
    points: list[tuple[float, str, int]],
) -> bytes:
    """Write one course file and return it as bytes.

    Args:
        course_name: The course name as shown on the watch (clipped to 15 bytes).
        geo: The ordered ``[lat, lng]`` polyline of the route.
        altitudes: Per-vertex altitude in metres, or ``None`` where unknown.
        points: ``(ground_m, label, course_point_type)`` warnings and marks,
            anchored by ground position along the route.

    Returns:
        The .fit bytes, proven to parse by reading them back.

    Raises:
        ValueError: when the route has no geometry.
    """
    if len(geo) < 2:
        raise ValueError("a course needs at least two points")
    cum = cumulative(geo)
    base = datetime.now(UTC).replace(microsecond=0)

    def stamp_at(ground_m: float) -> datetime:
        return base + timedelta(seconds=ground_m / _SYNTH_PACE_MS)

    def vertex_at(ground_m: float) -> int:
        return min(range(len(cum)), key=lambda i: abs(cum[i] - ground_m))

    point_at: dict[int, list[tuple[str, int]]] = {}
    for ground_m, label, kind in points:
        entries = point_at.setdefault(vertex_at(ground_m), [])
        if (label, kind) not in entries:  # variants that rejoin together say it once
            entries.append((label, kind))

    builder = FitFileBuilder(auto_define=True, min_string_size=32)

    file_id = FileIdMessage()
    file_id.type = FileType.COURSE
    file_id.manufacturer = Manufacturer.GARMIN.value
    file_id.product = 65534
    file_id.time_created = epoch_millis(base)
    file_id.serial_number = 12345
    builder.add(file_id)

    course = CourseMessage()
    course.course_name = clip_text(course_name)
    builder.add(course)

    start, stop = stamp_at(0.0), stamp_at(cum[-1])
    lap = LapMessage()
    lap.timestamp = epoch_millis(start)
    lap.start_time = epoch_millis(start)
    total_s = (stop - start).total_seconds()
    lap.total_elapsed_time = total_s
    lap.total_timer_time = total_s
    lap.total_distance = cum[-1]
    lap.start_position_lat = geo[0][0]
    lap.start_position_long = geo[0][1]
    lap.end_position_lat = geo[-1][0]
    lap.end_position_long = geo[-1][1]
    builder.add(lap)

    ev_start = EventMessage()
    ev_start.timestamp = epoch_millis(start)
    ev_start.event = Event.TIMER
    ev_start.event_type = EventType.START
    builder.add(ev_start)

    for index, (lat, lng) in enumerate(geo):
        stamp = stamp_at(cum[index])
        record = RecordMessage()
        record.timestamp = epoch_millis(stamp)
        record.position_lat = lat
        record.position_long = lng
        record.distance = cum[index]
        if altitudes[index] is not None:
            record.altitude = altitudes[index]
        builder.add(record)
        for label, kind in point_at.get(index, []):
            point = CoursePointMessage()
            point.timestamp = epoch_millis(stamp)
            point.position_lat = lat
            point.position_long = lng
            point.distance = cum[index]
            point.course_point_name = clip_text(label)
            point.type = kind
            builder.add(point)

    ev_stop = EventMessage()
    ev_stop.timestamp = epoch_millis(stop)
    ev_stop.event = Event.TIMER
    ev_stop.event_type = EventType.STOP
    builder.add(ev_stop)

    fit_file = builder.build()
    # round-trip in memory: the cheapest proof the binary is well-formed
    parsed = FitFile.from_bytes(fit_file.to_bytes())
    for record in parsed.records:
        if record.is_definition or record.message is None:
            continue
    return fit_file.to_bytes()


__all__ = [
    "CoursePoint",
    "build_course_bytes",
    "clip_text",
    "cumulative",
    "epoch_millis",
    "haversine_m",
]
