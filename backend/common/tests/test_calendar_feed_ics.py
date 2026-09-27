"""The hand-written RFC 5545 writer behind the task calendar feed.

Pure functions, no database: `common.calendar_feed` is imported by
`common.public_tokens` while settings load, so it must not need Django. The
scrubber for the feed URL is tested in `test_public_tokens.py`.
"""

from datetime import date, datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from common.calendar_feed import escape_text, fold_line, render_calendar


def _unfold(text):
    return text.replace("\r\n ", "")


def _event(**overrides):
    event = {
        "uid": "task-1@bottlecrm",
        "stamp": datetime(2026, 9, 27, 10, 15, tzinfo=dt_timezone.utc),
        "date": date(2026, 9, 30),
        "summary": "Call back",
        "description": "Priority: High\nhttps://app.example.com/tasks/1",
        "url": "https://app.example.com/tasks/1",
    }
    event.update(overrides)
    return event


class TestEscapeText:
    def test_escapes_the_four_specials(self):
        assert escape_text("a\\b;c,d\ne") == "a\\\\b\\;c\\,d\\ne"

    def test_every_line_break_form_becomes_one_escape(self):
        assert escape_text("a\r\nb\rc\nd") == "a\\nb\\nc\\nd"

    def test_backslash_is_escaped_before_the_others(self):
        """Escaping `;` first and `\\` second would double the new backslash."""
        assert escape_text(";") == "\\;"
        assert escape_text("\\;") == "\\\\\\;"

    def test_control_characters_are_dropped_but_tab_stays(self):
        assert escape_text("a\x00b\x07c\td\x7f") == "abc\td"

    def test_none_is_empty(self):
        assert escape_text(None) == ""


class TestFoldLine:
    def test_short_line_is_untouched(self):
        assert fold_line("SUMMARY:short") == "SUMMARY:short"

    def test_long_ascii_line_folds_at_75_octets(self):
        line = "SUMMARY:" + "x" * 200
        folded = fold_line(line)
        parts = folded.split("\r\n")
        assert len(parts) > 1
        assert all(len(p.encode("utf-8")) <= 75 for p in parts)
        assert len(parts[0].encode("utf-8")) == 75
        assert all(p.startswith(" ") for p in parts[1:])
        assert _unfold(folded) == line

    def test_multibyte_characters_are_never_split(self):
        # 3-byte and 4-byte characters, offset so a naive byte cut would land
        # inside one of them.
        line = "SUMMARY:a" + "€" * 40 + "\U0001f4c5" * 20
        folded = fold_line(line)
        parts = folded.split("\r\n")
        assert all(len(p.encode("utf-8")) <= 75 for p in parts)
        for part in parts:
            part.encode("utf-8").decode("utf-8")  # raises if a char was cut
        assert _unfold(folded) == line


class TestRenderCalendar:
    def test_envelope_and_crlf(self):
        body = render_calendar([])
        assert body.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
        assert "PRODID:" in body
        assert "CALSCALE:GREGORIAN\r\n" in body
        assert "X-WR-CALNAME:BottleCRM tasks\r\n" in body
        assert body.endswith("END:VCALENDAR\r\n")
        assert "\n" not in body.replace("\r\n", "")

    def test_all_day_event(self):
        body = render_calendar([_event()])
        assert "UID:task-1@bottlecrm\r\n" in body
        assert "DTSTAMP:20260927T101500Z\r\n" in body
        assert "DTSTART;VALUE=DATE:20260930\r\n" in body
        assert "DTEND;VALUE=DATE:20261001\r\n" in body
        assert "SUMMARY:Call back\r\n" in body
        assert "URL:https://app.example.com/tasks/1\r\n" in body
        assert (
            "DESCRIPTION:Priority: High\\nhttps://app.example.com/tasks/1\r\n" in body
        )

    def test_dtend_crosses_the_year(self):
        body = render_calendar([_event(date=date(2026, 12, 31))])
        assert "DTSTART;VALUE=DATE:20261231\r\n" in body
        assert "DTEND;VALUE=DATE:20270101\r\n" in body

    def test_dtstamp_is_converted_to_utc(self):
        stamp = datetime(2026, 9, 27, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        body = render_calendar([_event(stamp=stamp)])
        assert "DTSTAMP:20260927T043000Z\r\n" in body

    def test_summary_is_escaped_and_folded(self):
        title = "Renew, sign; send\\file " + "é" * 80
        body = render_calendar([_event(summary=title)])
        for line in body.split("\r\n"):
            assert len(line.encode("utf-8")) <= 75
        assert f"SUMMARY:{escape_text(title)}" in _unfold(body)

    def test_a_title_cannot_inject_a_property(self):
        """A newline in a title must not start a new content line."""
        body = render_calendar([_event(summary="x\r\nATTENDEE:mailto:a@b.c")])
        assert "\r\nATTENDEE" not in _unfold(body)
