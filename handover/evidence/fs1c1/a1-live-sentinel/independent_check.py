"""Independent check of the unapplied sentinel candidate (harness, not Kriya): the goal's two stated behaviours."""
import datetime
import sys

sys.path.insert(0, sys.argv[1])
from freezegun import freeze_time  # noqa: E402 - after the candidate root is on the path
from freezegun.api import _parse_time_to_freeze  # noqa: E402

for value, expected in ((0, datetime.datetime(1970, 1, 1)), (86400.5, datetime.datetime(1970, 1, 2, 0, 0, 0, 500000))):
    print(f"_parse_time_to_freeze({value!r}) ->", repr(_parse_time_to_freeze(value)))
    try:
        with freeze_time(value):
            now = datetime.datetime.now()
        print(f"freeze_time({value!r}) -> now={now!r} {'OK' if now == expected else 'WRONG'}")
    except Exception as error:
        print(f"freeze_time({value!r}) -> {type(error).__name__}: {error}")
