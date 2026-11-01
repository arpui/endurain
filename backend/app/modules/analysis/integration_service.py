"""Published surface of the training-analysis module.

Consumers get numbers from here, never from the module's helpers: the shared aRSS
kernel, the athlete's thresholds and the stream reading that feeds them are owned
by analysis, and what other modules need is the aggregate, not the machinery.
Ownership checks stay with the caller - these reads describe the activity's own
samples and trust the caller to have scoped the request.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.orm import Session

import modules.analysis.contracts as analysis_contracts
import modules.analysis.service as analysis_service


def window_stream_stats(
    activity_id: int,
    user_id: int,
    windows: Sequence[tuple[datetime, datetime]],
    db: Session,
) -> list[analysis_contracts.WindowStats]:
    """Return mean power, heart rate and aRSS per minute for time windows of one activity.

    Args:
        activity_id: The activity whose streams are read.
        user_id: The athlete whose settings (thresholds, parameters) evaluate the
            windows; usually the activity's owner.
        windows: ``(start, end)`` wall-clock pairs in the streams' own clock, in
            any order; naive stream times are taken as UTC.
        db: Database session.

    Returns:
        One :class:`~modules.analysis.contracts.WindowStats` per window, in the
        order given. Windows with nothing behind them read ``None`` fields rather
        than zeroes.
    """
    return analysis_service.compute_window_stats(activity_id, user_id, windows, db)
