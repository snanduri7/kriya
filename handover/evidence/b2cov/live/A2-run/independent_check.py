"""Independent check of the A2 B2-COV sentinel candidate (harness, not Kriya). Diagnosis only - never an oracle."""
import datetime
import sys

sys.path.insert(0, sys.argv[1])
from freezegun.api import _parse_tz_offset  # noqa: E402

for value in ("+05:30", "-02:00", "abc", "+5:30", "+05:30\n", datetime.timedelta(hours=3), 4, -1.5):
    try:
        print(f"_parse_tz_offset({value!r}) ->", repr(_parse_tz_offset(value)))
    except Exception as error:
        print(f"_parse_tz_offset({value!r}) ->", type(error).__name__, error)
