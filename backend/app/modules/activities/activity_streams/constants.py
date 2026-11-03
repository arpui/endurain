"""Constants for activity stream types and power-stream calibration."""

STREAM_TYPE_HR: int = 1
STREAM_TYPE_POWER: int = 2
STREAM_TYPE_CADENCE: int = 3
STREAM_TYPE_ELEVATION: int = 4
STREAM_TYPE_SPEED: int = 5
STREAM_TYPE_PACE: int = 6
STREAM_TYPE_MAP: int = 7
STREAM_TYPE_TEMPERATURE: int = 8

# Power-stream provenance (stored on activities_streams.power_source).
POWER_SOURCE_STRYD: str = "stryd"  # pod reading carried in a FIT developer field
POWER_SOURCE_DEVICE: str = "device"  # a standard power field from the watch/head unit

# Multiplicative recalibration applied at READ time to stryd-sourced power so it
# lands on the athlete's current (native) scale. The stored waypoints stay raw:
# this is a display/derivation transform, and the calibrated value is flagged
# with the stream's ``power_source`` so the UI can mark it. The constant is the
# pooled least-squares factor from devdocs §28 (W_native = W_stryd / 0.705); the
# per-era refinement is deliberately not applied because it is inseparable from
# real age/economy drift on this data (see §28).
STRYD_POWER_SCALE: float = 1.418
