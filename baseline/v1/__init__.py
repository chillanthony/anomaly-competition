"""v1 rule/statistics detector for the CCF AIOps 2026 phase-1 dataset.

This package deliberately has no third-party dependency. It reads the same
`processed/` CSV layout that the public `sample/` bundles use and the same
`<region>_<range>_data/` layout that the phase-1 dataset uses, so one code path
covers both offline regression on the labelled sample and the full run on the
server.
"""

from .detector import Detection, detect_events
from .sources import load_series, SeriesKey

__all__ = ["Detection", "detect_events", "load_series", "SeriesKey"]
