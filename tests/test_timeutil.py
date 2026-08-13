import re
import zoneinfo

from mia.timeutil import local_iana_timezone, local_timezone_label, local_utc_offset


def test_iana_timezone_is_a_real_zone_or_none():
    zone = local_iana_timezone()
    if zone is not None:
        zoneinfo.ZoneInfo(zone)  # must not raise


def test_utc_offset_is_well_formed():
    assert re.fullmatch(r"UTC[+-]\d{2}:\d{2}", local_utc_offset())


def test_label_falls_back_to_offset_when_no_iana_zone(monkeypatch):
    monkeypatch.setattr("mia.timeutil.local_iana_timezone", lambda: None)
    assert local_timezone_label() == local_utc_offset()
