"""Independent check of the applied A2 candidate (harness, not Kriya): the goal's stated behaviour, the preserved
inputs, and the integration through freeze_time(tz_offset=...)."""
import datetime
import sys

sys.path.insert(0, sys.argv[1])
from freezegun import freeze_time  # noqa: E402
from freezegun.api import _parse_tz_offset  # noqa: E402


def show(label, call):
    try:
        print(label, "->", repr(call()))
    except Exception as error:
        print(label, "->", type(error).__name__, error)


for value in ("+05:30", "-02:00", "+00:00", "-05:30", "abc", "05:30", "+5:30", "+05:3", "+05:30x", ""):
    show(f"_parse_tz_offset({value!r})", lambda v=value: _parse_tz_offset(v))
for value in (datetime.timedelta(hours=3), 4, -1.5, 0):
    show(f"_parse_tz_offset({value!r})", lambda v=value: _parse_tz_offset(v))
with freeze_time("2012-01-14 12:00:00", tz_offset="+05:30"):
    print("freeze_time(tz_offset='+05:30') now ->", datetime.datetime.now())
with freeze_time("2012-01-14 12:00:00", tz_offset="-02:00"):
    print("freeze_time(tz_offset='-02:00') now ->", datetime.datetime.now())
