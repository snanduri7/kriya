"""Independent post-run check of A1 (not Kriya; run on a temp copy of the applied workspace with a
python-dateutil venv): the goal's own examples against the applied candidate."""
import datetime
import sys

sys.path.insert(0, ".")
from freezegun import freeze_time  # noqa: E402
from freezegun.api import _parse_time_to_freeze  # noqa: E402

for value in (0, 86400.5):
    print(f"_parse_time_to_freeze({value!r}) ->", repr(_parse_time_to_freeze(value)))
    try:
        with freeze_time(value):
            print(f"freeze_time({value!r}): now ->", datetime.datetime.now())
    except Exception as error:  # the goal requires this to freeze time
        print(f"freeze_time({value!r}) raised", type(error).__name__)
