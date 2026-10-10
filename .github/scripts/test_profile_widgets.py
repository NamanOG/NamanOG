from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from update_profile import (
    CalendarParser, Day, activity_svg, calendar_svg, compute_streaks, streak_svg, validate_svg, write_svg
)


class ProfileWidgetTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 10)
        self.days = [Day(self.today - timedelta(days=30-i), i % 5, i % 5) for i in range(31)]

    def calendar_html(self):
        return ''.join(f'<td id="day-{i}" data-date="{day.date}" data-level="{day.level}"></td>'
                       f'<tool-tip for="day-{i}">{day.count} contributions on October 1st.</tool-tip>'
                       for i, day in enumerate(self.days))

    def test_dates_and_accessible_counts_are_matched(self):
        parser = CalendarParser()
        parser.feed(self.calendar_html().replace('0 contributions', 'No contributions'))
        self.assertEqual(parser.days(self.today), self.days)

    def test_incomplete_calendar_is_rejected(self):
        parser = CalendarParser()
        parser.feed(self.calendar_html().replace('data-date="2026-10-10"', 'data-date="2026-10-11"'))
        with self.assertRaises(ValueError):
            parser.days(self.today)

    def test_http_error_body_cannot_overwrite_a_valid_image(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'activity.svg'
            original = activity_svg(self.days, 'light')
            write_svg(path, original)
            with self.assertRaises(Exception):
                write_svg(path, b'<html>Payment Required</html>')
            self.assertEqual(path.read_bytes(), original)

    def test_zero_activity_does_not_divide_by_zero(self):
        days = [Day(day.date, 0, 0) for day in self.days]
        for theme in ('light', 'dark'):
            data = activity_svg(days, theme)
            validate_svg(data)
            self.assertIn(b'0 contributions over 31 days', data)
            validate_svg(calendar_svg(days, theme))
            validate_svg(streak_svg(days, theme, self.today))

    def test_compute_streaks(self):
        # 3 consecutive active days leading to today
        days = [Day(self.today - timedelta(days=i), 2 if i < 3 else 0, 1) for i in range(10)]
        days.sort(key=lambda d: d.date)
        current, total, longest = compute_streaks(days, self.today)
        self.assertEqual(current, 3)
        self.assertEqual(total, 6)
        self.assertEqual(longest, 3)

    def test_active_content_and_external_images_are_rejected(self):
        for child in ('<script>alert(1)</script>', '<image href="https://example.com/track"/>', '<rect onclick="alert(1)"/>'):
            with self.assertRaises(ValueError):
                validate_svg(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">{child}</svg>'.encode())

    def test_trophy_error_svg_is_rejected(self):
        data = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><text>Rate limit exceeded</text></svg>'
        with self.assertRaises(ValueError):
            validate_svg(data, ('Commits', 'Repositories', 'Stars'))


if __name__ == '__main__':
    unittest.main()
