"""Test merging of embedded VTIMEZONE components with equivalent rules.

A calendar may contain several VTIMEZONE components whose TZIDs differ while
their transition rules describe the same local time line.
``Calendar.merge_equivalent_timezones`` keeps one definition per equivalence
class, rewrites all TZID references, and must preserve the UTC instant of
every date-time, including historical offsets and the DST fold and jump
boundaries.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from icalendar import Calendar, Timezone, TimezoneStandard, vDDDLists

UTC = timezone.utc

NY_STANDARD = (
    "BEGIN:STANDARD\r\n"
    "DTSTART:19701101T020000\r\n"
    "TZOFFSETFROM:-0400\r\n"
    "TZOFFSETTO:-0500\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\n"
    "TZNAME:EST\r\n"
    "END:STANDARD\r\n"
)
NY_DAYLIGHT = (
    "BEGIN:DAYLIGHT\r\n"
    "DTSTART:19700308T020000\r\n"
    "TZOFFSETFROM:-0500\r\n"
    "TZOFFSETTO:-0400\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
    "TZNAME:EDT\r\n"
    "END:DAYLIGHT\r\n"
)

# Same rules but spelled with RDATEs covering 2023-2025 and other labels.
NY_SHORT_STANDARD = (
    "BEGIN:STANDARD\r\n"
    "DTSTART:20231105T020000\r\n"
    "TZOFFSETFROM:-0400\r\n"
    "TZOFFSETTO:-0500\r\n"
    "RDATE:20241103T020000,20251102T020000\r\n"
    "TZNAME:Eastern Standard\r\n"
    "END:STANDARD\r\n"
)
NY_SHORT_DAYLIGHT = (
    "BEGIN:DAYLIGHT\r\n"
    "DTSTART:20230312T020000\r\n"
    "TZOFFSETFROM:-0500\r\n"
    "TZOFFSETTO:-0400\r\n"
    "RDATE:20240310T020000,20250309T020000\r\n"
    "TZNAME:Eastern Daylight\r\n"
    "END:DAYLIGHT\r\n"
)

LONDON_STANDARD = (
    "BEGIN:STANDARD\r\n"
    "DTSTART:19701025T030000\r\n"
    "TZOFFSETFROM:+0100\r\n"
    "TZOFFSETTO:+0000\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\n"
    "TZNAME:GMT\r\n"
    "END:STANDARD\r\n"
)
LONDON_DAYLIGHT = (
    "BEGIN:DAYLIGHT\r\n"
    "DTSTART:19700322T020000\r\n"
    "TZOFFSETFROM:+0000\r\n"
    "TZOFFSETTO:+0100\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\n"
    "TZNAME:BST\r\n"
    "END:DAYLIGHT\r\n"
)

NY = "X-Merge/New_York"
NY_COPY = "X-Merge/New_York_Copy"
NY_SHORT = "X-Merge/New_York_Short"
NY_THIRD = "X-Merge/New_York_Third"
LONDON = "X-Merge/London"
DUP = "X-Merge/Duplicate_Name"
BROKEN = "X-Merge/Broken"


def vtimezone(tzid, *subcomponents):
    """Build a VTIMEZONE component string."""
    return (
        f"BEGIN:VTIMEZONE\r\nTZID:{tzid}\r\n{''.join(subcomponents)}END:VTIMEZONE\r\n"
    )


def event(uid, dtstart, tzid=None):
    """Build a VEVENT string, with a TZID parameter when a tzid is given."""
    parameter = f";TZID={tzid}" if tzid is not None else ""
    return (
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART{parameter}:{dtstart}\r\nEND:VEVENT\r\n"
    )


def calendar(*blocks, x_wr_timezone=None):
    """Assemble a calendar from raw VTIMEZONE/component strings."""
    head = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//merge-test//EN\r\n"
    if x_wr_timezone is not None:
        head += f"X-WR-TIMEZONE:{x_wr_timezone}\r\n"
    return Calendar.from_ical(head + "".join(blocks) + "END:VCALENDAR\r\n")


def aware_utc_moments(cal):
    """All TZID referenced date-times in the calendar, as a multiset of UTC times.

    A multiset is used because serialization normalizes the order of
    properties on a component.
    """
    result = []
    for component in cal.walk():
        if component.name in ("VTIMEZONE", "STANDARD", "DAYLIGHT"):
            continue
        for _name, value in component.property_items(recursive=False, sorted=False):
            params = getattr(value, "params", None)
            if params is None or params.get("TZID") is None:
                continue
            items = value.dts if isinstance(value, vDDDLists) else (value,)
            for item in items:
                dt = getattr(item, "dt", None)
                halves = dt if isinstance(dt, tuple) else (dt,)
                for half in halves:
                    if isinstance(half, datetime) and half.tzinfo is not None:
                        result.append(half.astimezone(UTC))
    return Counter(result)


@pytest.fixture
def ny_timezone():
    return vtimezone(NY, NY_STANDARD, NY_DAYLIGHT)


@pytest.fixture
def ny_copy_timezone():
    # identical rule, different labels inside the subcomponents
    standard = NY_STANDARD.replace("TZNAME:EST", "TZNAME:Eastern Std")
    daylight = NY_DAYLIGHT.replace("TZNAME:EDT", "TZNAME:Eastern DST")
    return vtimezone(NY_COPY, standard, daylight)


@pytest.fixture
def ny_short_timezone():
    return vtimezone(NY_SHORT, NY_SHORT_STANDARD, NY_SHORT_DAYLIGHT)


@pytest.fixture
def london_timezone():
    return vtimezone(LONDON, LONDON_STANDARD, LONDON_DAYLIGHT)


def test_rrule_and_rdate_definitions_are_merged(tzp, ny_timezone, ny_short_timezone):
    """RRULE and RDATE spellings of the same rules collapse into one."""
    cal = calendar(
        ny_timezone,
        ny_short_timezone,
        event("summer", "20240715T120000", NY),
        event("winter", "20240115T120000", NY_SHORT),
    )
    before = aware_utc_moments(cal)

    renamed = cal.merge_equivalent_timezones()

    assert renamed == {NY_SHORT: NY}
    assert [tz.tz_name for tz in cal.timezones] == [NY]
    assert NY_SHORT not in cal.get_used_tzids()
    assert cal.get_missing_tzids() == set()

    merged = cal.to_ical().decode()
    assert NY_SHORT not in merged
    assert "BEGIN:VTIMEZONE" in merged

    reparsed = Calendar.from_ical(merged)
    assert aware_utc_moments(reparsed) == before
    assert [tz.tz_name for tz in reparsed.timezones] == [NY]


def test_fold_jump_and_historical_offsets_keep_their_semantics(
    tzp, ny_timezone, ny_copy_timezone
):
    """Date-times on and around DST boundaries and in history stay identical."""
    boundary_starts = (
        "20240310T020000",  # exact spring-forward onset (jump starts)
        "20240310T023000",  # non-existent local time inside the jump
        "20241103T013000",  # ambiguous local time inside the fold
        "20241103T020000",  # exact fall-back onset
        "20240715T120000",  # daylight time
        "19720115T120000",  # historical standard offset
    )
    blocks = [ny_timezone, ny_copy_timezone]
    blocks.extend(
        event(f"boundary-{index}", value, NY_COPY if index % 2 else NY)
        for index, value in enumerate(boundary_starts)
    )
    cal = calendar(*blocks)
    before = aware_utc_moments(cal)

    renamed = cal.merge_equivalent_timezones()

    assert renamed == {NY_COPY: NY}
    reparsed = Calendar.from_ical(cal.to_ical())
    assert aware_utc_moments(reparsed) == before


def test_different_rules_are_not_merged(tzp, ny_timezone, london_timezone):
    """Definitions with different offset lines remain separate."""
    cal = calendar(
        ny_timezone,
        london_timezone,
        event("in-new-york", "20240715T120000", NY),
        event("in-london", "20240715T120000", LONDON),
    )
    before = aware_utc_moments(cal)

    assert cal.merge_equivalent_timezones() == {}
    assert sorted(tz.tz_name for tz in cal.timezones) == [LONDON, NY]

    reparsed = Calendar.from_ical(cal.to_ical())
    assert aware_utc_moments(reparsed) == before


def test_short_definition_is_merged_only_when_usage_stays_in_its_range(
    tzp, ny_timezone, ny_short_timezone
):
    """Different history blocks a merge only when history is actually used."""
    # an event in 2020 (covered by the RRULE zone, not by the RDATE zone)
    cal = calendar(
        ny_timezone,
        ny_short_timezone,
        event("old", "20200115T120000", NY),
        event("new", "20240715T120000", NY_SHORT),
    )
    assert cal.merge_equivalent_timezones() == {}
    assert len(cal.timezones) == 2

    # only 2024 dates are in use: the short definition covers everything used
    cal = calendar(
        ny_timezone,
        ny_short_timezone,
        event("winter", "20240115T120000", NY),
        event("summer", "20240715T120000", NY_SHORT),
    )
    before = aware_utc_moments(cal)
    renamed = cal.merge_equivalent_timezones()
    assert renamed == {NY_SHORT: NY}
    reparsed = Calendar.from_ical(cal.to_ical())
    assert aware_utc_moments(reparsed) == before


def test_survivor_is_the_smallest_tzid_and_result_is_stable(
    tzp, ny_timezone, ny_copy_timezone, ny_short_timezone
):
    """The canonical TZID is deterministic regardless of component order."""
    third = vtimezone(NY_THIRD, NY_STANDARD, NY_DAYLIGHT)
    events = [
        event("one", "20240715T120000", NY),
        event("two", "20240715T120000", NY_COPY),
        event("three", "20240715T120000", NY_SHORT),
        event("four", "20240715T120000", NY_THIRD),
    ]

    first = calendar(ny_short_timezone, third, ny_timezone, ny_copy_timezone, *events)
    second = calendar(ny_copy_timezone, ny_timezone, third, ny_short_timezone, *events)

    expected_rename = {NY_COPY: NY, NY_SHORT: NY, NY_THIRD: NY}
    assert first.merge_equivalent_timezones() == expected_rename
    assert second.merge_equivalent_timezones() == expected_rename
    assert [tz.tz_name for tz in first.timezones] == [NY]
    assert [tz.tz_name for tz in second.timezones] == [NY]
    # the merge is idempotent
    assert first.merge_equivalent_timezones() == {}
    assert aware_utc_moments(first) == aware_utc_moments(second)


def test_merge_without_timezones_or_with_one_is_a_noop(tzp, ny_timezone):
    """Calendars with fewer than two VTIMEZONEs are returned unchanged."""
    cal = calendar(event("utc", "20240715T120000Z"))
    assert cal.merge_equivalent_timezones() == {}
    assert cal.timezones == []

    cal = calendar(ny_timezone, event("one", "20240715T120000", NY))
    assert cal.merge_equivalent_timezones() == {}
    assert [tz.tz_name for tz in cal.timezones] == [NY]


def test_unused_equivalent_timezones_are_still_merged(
    tzp, ny_timezone, ny_copy_timezone
):
    """Duplicate definitions without any references collapse as well."""
    cal = calendar(ny_timezone, ny_copy_timezone, event("utc", "20240715T120000Z"))
    assert cal.merge_equivalent_timezones() == {NY_COPY: NY}
    assert [tz.tz_name for tz in cal.timezones] == [NY]


def test_all_kinds_of_tzid_references_are_rewritten(tzp, ny_timezone):
    """TZID parameters anywhere in the calendar are rewritten together."""
    other = vtimezone(
        NY_COPY,
        NY_STANDARD.replace("TZNAME:EST", "TZNAME:Eastern Std"),
        NY_DAYLIGHT.replace("TZNAME:EDT", "TZNAME:Eastern DST"),
    )
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//merge-test//EN\r\n"
        f"{ny_timezone}{other}"
        "BEGIN:VEVENT\r\n"
        "UID:recurring\r\n"
        f"DTSTART;TZID={NY_COPY}:20240115T120000\r\n"
        f"EXDATE;TZID={NY_COPY}:20240122T120000,20240129T120000\r\n"
        f"RDATE;TZID={NY_COPY}:20240205T120000\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VTODO\r\n"
        "UID:todo\r\n"
        f"DUE;TZID={NY_COPY}:20240715T120000\r\n"
        "END:VTODO\r\n"
        "END:VCALENDAR\r\n"
    )
    cal = Calendar.from_ical(ics)
    before = aware_utc_moments(cal)

    assert cal.merge_equivalent_timezones() == {NY_COPY: NY}

    output = cal.to_ical().decode()
    assert f"TZID={NY_COPY}" not in output
    reparsed = Calendar.from_ical(output)
    assert reparsed.get_missing_tzids() == set()
    assert aware_utc_moments(reparsed) == before


def test_x_wr_timezone_is_rewritten(tzp, ny_timezone, ny_copy_timezone):
    """The calendar default timezone follows the rename."""
    cal = calendar(
        ny_timezone,
        ny_copy_timezone,
        event("one", "20240715T120000", NY_COPY),
        x_wr_timezone=NY_COPY,
    )
    cal.merge_equivalent_timezones()
    assert str(cal.get("X-WR-TIMEZONE")) == NY


def test_identically_named_duplicates(tzp):
    """Same TZID: equivalent copies are deduplicated, conflicts are kept."""
    cal = calendar(
        vtimezone(DUP, NY_STANDARD, NY_DAYLIGHT),
        vtimezone(DUP, NY_STANDARD, NY_DAYLIGHT),
        event("one", "20240715T120000", DUP),
    )
    before = aware_utc_moments(cal)
    assert cal.merge_equivalent_timezones() == {}
    assert [tz.tz_name for tz in cal.timezones] == [DUP]
    assert aware_utc_moments(Calendar.from_ical(cal.to_ical())) == before

    # two different rules sharing one TZID: nothing may be removed
    cal = calendar(
        vtimezone(DUP, NY_STANDARD, NY_DAYLIGHT),
        vtimezone(DUP, LONDON_STANDARD, LONDON_DAYLIGHT),
        event("one", "20240715T120000", DUP),
    )
    assert cal.merge_equivalent_timezones() == {}
    assert len(cal.timezones) == 2


def test_unparseable_vtimezone_is_never_removed(tzp, ny_timezone):
    """Definitions that cannot be evaluated are left untouched."""
    cal = calendar(ny_timezone, event("one", "20240715T120000", NY))
    broken = Timezone()
    broken.add("TZID", BROKEN)
    # a STANDARD subcomponent without DTSTART/offsets cannot be evaluated
    broken.add_component(TimezoneStandard())
    cal.add_component(broken)

    assert cal.merge_equivalent_timezones() == {}
    assert sorted(tz.tz_name for tz in cal.timezones) == [BROKEN, NY]


def test_equivalent_rules_unit(tzp):
    """Direct checks of the rule comparison."""
    full = Timezone.from_ical(vtimezone(NY, NY_STANDARD, NY_DAYLIGHT))
    short = Timezone.from_ical(
        vtimezone(NY_SHORT, NY_SHORT_STANDARD, NY_SHORT_DAYLIGHT)
    )
    london = Timezone.from_ical(vtimezone(LONDON, LONDON_STANDARD, LONDON_DAYLIGHT))

    assert Timezone.equivalent_rules(full, full)
    # globally the sequences differ (one is much longer)
    assert not Timezone.equivalent_rules(full, short)
    # but they agree everywhere the short definition is actually used
    assert Timezone.equivalent_rules(
        full, short, datetime(2024, 1, 1), datetime(2024, 12, 31)
    )
    # and disagree around times the short definition does not cover
    assert not Timezone.equivalent_rules(
        full, short, datetime(1990, 1, 1), datetime(1990, 12, 31)
    )
    assert not Timezone.equivalent_rules(
        full, london, datetime(2024, 1, 1), datetime(2024, 12, 31)
    )
    onsets = full.get_onsets()
    assert onsets == tuple(sorted(onsets, key=lambda onset: onset.onset))
    # spring forward grows the offset, fall back shrinks it
    spring = next(onset for onset in onsets if onset.onset == datetime(2024, 3, 10, 2))
    fall = next(onset for onset in onsets if onset.onset == datetime(2024, 11, 3, 2))
    assert spring.offset_to - spring.offset_from == timedelta(hours=1)
    assert fall.offset_to - fall.offset_from == timedelta(hours=-1)
    assert full.get_initial_offset() == timedelta(hours=-5)


def test_margin_covers_fold_interval(tzp, ny_timezone, ny_copy_timezone):
    """A referenced time inside a fold sees the onset just after it."""
    full = Timezone.from_ical(ny_timezone)
    copy = Timezone.from_ical(ny_copy_timezone)
    # 2024-11-03 01:30 is ambiguous: its fold ends at the 02:00 onset, which
    # lies outside the used range but must still be compared via the margin
    assert Timezone.equivalent_rules(
        full,
        copy,
        datetime(2024, 11, 3, 1, 30),
        datetime(2024, 11, 3, 1, 30),
    )
