"""Refresh profile SVGs from public data, preserving valid files on outages.

Uses only Python's standard library. No access token or external package required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import math
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USERNAME = 'NamanOG'
ROOT = Path(__file__).resolve().parents[2]
STATS = ROOT / 'stats'
SVG_NS = 'http://www.w3.org/2000/svg'
# Both mirrors are listed in the upstream project's README and were tested.
TROPHY_HOSTS = (
    'https://trophygithubreadmelang.cybee.dpdns.org/',
    'https://trophy.ryglcloud.net/',
)
THEMES = {
    'light': {
        'text': '#1f2328', 'muted': '#656d76', 'sub': '#656d76',
        'grid': '#d0d7de', 'bar': '#57606a',
        'dots': ('#ebedf0', '#d3e5d7', '#9fc9a8', '#5d9468', '#32663e'),
        'card_bg': '#ffffff', 'border': '#d0d7de', 'divider': '#eaeef2',
        'track': '#f6f8fa', 'spark_fill': '#57606a', 'today': '#1f2328',
    },
    'dark': {
        'text': '#f0f6fc', 'muted': '#7d8590', 'sub': '#7d8590',
        'grid': '#30363d', 'bar': '#8b949e',
        'dots': ('#161b22', '#193a23', '#2c643d', '#448457', '#76a782'),
        'card_bg': '#0d1117', 'border': '#30363d', 'divider': '#21262d',
        'track': '#161b22', 'spark_fill': '#8b949e', 'today': '#f0f6fc',
    },
}


@dataclass(frozen=True)
class Day:
    date: date
    count: int
    level: int


class CalendarParser(HTMLParser):
    """Match GitHub's date cells to their accessible contribution-count tooltips."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: dict[str, tuple[date, int]] = {}
        self.labels: dict[str, str] = {}
        self.tooltip: str | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == 'td' and attributes.get('data-date'):
            cell_id = attributes.get('id')
            if not cell_id:
                raise ValueError('Contribution cell is missing its identifier')
            self.cells[cell_id] = (date.fromisoformat(attributes['data-date']), int(attributes['data-level']))
        if tag == 'tool-tip' and attributes.get('for'):
            self.tooltip = attributes['for']
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.tooltip is not None:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == 'tool-tip' and self.tooltip is not None:
            self.labels[self.tooltip] = ''.join(self.text).strip()
            self.tooltip = None

    def days(self, today: date) -> list[Day]:
        result: list[Day] = []
        for cell_id, (day_date, level) in self.cells.items():
            if day_date > today:
                continue
            label = self.labels.get(cell_id, '')
            match = re.match(r'(No|[\d,]+) contributions? on ', label)
            if not match or not 0 <= level <= 4:
                raise ValueError(f'Unrecognized contribution data for {day_date}')
            count = 0 if match[1] == 'No' else int(match[1].replace(',', ''))
            result.append(Day(day_date, count, level))
        result.sort(key=lambda day: day.date)
        if len({day.date for day in result}) != len(result):
            raise ValueError('Duplicate contribution dates')
        expected = [today - timedelta(days=offset) for offset in range(30, -1, -1)]
        if [day.date for day in result if day.date >= expected[0]] != expected:
            raise ValueError('GitHub did not return a complete recent calendar')
        return result


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': 'NamanOG-profile-widgets/1.0'})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError('Widget response exceeds the size limit')
    return data


def validate_svg(data: bytes, required_labels: tuple[str, ...] = ()) -> None:
    root = ET.fromstring(data)
    if root.tag != f'{{{SVG_NS}}}svg' or not root.get('viewBox'):
        raise ValueError('Response is not a complete SVG')
    for element in root.iter():
        if element.tag.rsplit('}', 1)[-1] in {'script', 'foreignObject'}:
            raise ValueError('SVG contains active content')
        for key, value in element.attrib.items():
            if key.rsplit('}', 1)[-1].startswith('on'):
                raise ValueError('SVG contains an event handler')
            if key.rsplit('}', 1)[-1] == 'href' and not (value.startswith('#') or value.startswith('data:image/')):
                raise ValueError('SVG references an external resource')
    text = ' '.join(root.itertext())
    if any(label not in text for label in required_labels):
        raise ValueError('Trophy response is missing expected milestones')


def write_svg(path: Path, data: bytes) -> None:
    validate_svg(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.svg.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def svg_document(width: int, height: int, title: str, description: str, body: str) -> bytes:
    return (f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>'
            '<style>'
            'text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;font-feature-settings:"tnum" 1}'
            '.mono,.lbl{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:9.5px;font-weight:600;letter-spacing:0.08em}'
            '.sub{font-size:11.5px}'
            '</style>'
            f'{body}</svg>').encode('utf-8')


def activity_svg(days: list[Day], theme: str) -> bytes:
    recent = days[-31:]
    colors = THEMES[theme]
    total = sum(day.count for day in recent)
    maximum = max(1, max(day.count for day in recent))
    body = [f'<text x="4" y="24" fill="{colors["text"]}" font-size="20" font-weight="600">{total:,} contributions over 31 days</text>']
    body.append(f'<line x1="4" y1="128" x2="596" y2="128" stroke="{colors["grid"]}"/>')
    for index, day in enumerate(recent):
        height = round(day.count / maximum * 78, 2)
        x = 8 + index * 19
        body.append(f'<rect x="{x}" y="{128-height}" width="12" height="{height}" rx="1" fill="{colors["bar"]}"><title>{day.date}: {day.count} contributions</title></rect>')
    for x, day, anchor in ((4, recent[0], 'start'), (596, recent[-1], 'end')):
        body.append(f'<text x="{x}" y="151" text-anchor="{anchor}" font-size="14" fill="{colors["muted"]}">{day.date:%d %b %Y}</text>')
    description = f'GitHub contributions from {recent[0].date} through {recent[-1].date}. Total {total}. Refreshed {recent[-1].date} UTC. '
    description += '; '.join(f'{day.date}: {day.count}' for day in recent)
    return svg_document(600, 164, f'{USERNAME}: recent contributions', description, ''.join(body))


def calendar_svg(days: list[Day], theme: str) -> bytes:
    colors = THEMES[theme]
    start = days[0].date - timedelta(days=(days[0].date.weekday() + 1) % 7)
    columns = (days[-1].date - start).days // 7 + 1
    body: list[str] = []
    for day in days:
        offset = (day.date - start).days
        x, y = 16 + offset // 7 * 16, 12 + offset % 7 * 16
        body.append(f'<rect x="{x}" y="{y}" width="11" height="11" rx="2" fill="{colors["dots"][day.level]}"><title>{day.date}: {day.count} contributions</title></rect>')
    return svg_document(columns * 16 + 28, 132, f'{USERNAME}: contribution calendar',
                        f'Contribution calendar through {days[-1].date}; static alternative to the contribution snake.', ''.join(body))


def compute_streaks(days: list[Day], today: date) -> tuple[int, int, int]:
    total = sum(d.count for d in days)
    check_days = list(reversed(days))
    if check_days and check_days[0].date == today and check_days[0].count == 0:
        check_days = check_days[1:]
    current_streak = 0
    for d in check_days:
        if d.count > 0:
            current_streak += 1
        else:
            break
    longest_streak = 0
    curr = 0
    for d in days:
        if d.count > 0:
            curr += 1
            longest_streak = max(longest_streak, curr)
        else:
            curr = 0
    return current_streak, total, longest_streak


def streak_svg(days: list[Day], theme: str, today: date) -> bytes:
    colors = THEMES[theme]
    curr_streak, total_contribs, longest_streak = compute_streaks(days, today)
    width = 600
    height = 108

    # Active streak breakdown
    check_days = list(reversed(days))
    if check_days and check_days[0].date == today and check_days[0].count == 0:
        check_days = check_days[1:]
    streak_days = []
    for d in check_days:
        if d.count > 0:
            streak_days.append(d)
        else:
            break
    streak_days.reverse()
    streak_commits = sum(d.count for d in streak_days)
    start_str = streak_days[0].date.strftime('%d %b') if streak_days else today.strftime('%d %b')
    rate = round(total_contribs / 365, 1)

    recent_14 = days[-14:] if len(days) >= 14 else days
    active_in_14 = sum(1 for d in recent_14 if d.count > 0)
    pace_pct = round(active_in_14 / len(recent_14) * 100) if recent_14 else 0

    if curr_streak > 0 and curr_streak >= longest_streak:
        streak_sub = f'Started {start_str} · Personal best'
    elif curr_streak > 0:
        streak_sub = f'Started {start_str} · {streak_commits} commits'
    else:
        streak_sub = 'No active streak'

    body: list[str] = [
        f'<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="8" fill="{colors["card_bg"]}" stroke="{colors["border"]}"/>',
        f'<line x1="200" y1="18" x2="200" y2="90" stroke="{colors["divider"]}" stroke-width="1"/>',
        f'<line x1="392" y1="18" x2="392" y2="90" stroke="{colors["divider"]}" stroke-width="1"/>',
        # Col 1
        '<g transform="translate(24, 18)">',
        f'<text x="0" y="11" fill="{colors["muted"]}" class="lbl">CURRENT STREAK</text>',
        f'<text x="0" y="47" fill="{colors["text"]}" font-size="34" font-weight="700" letter-spacing="-0.03em">{curr_streak}<tspan font-size="14" font-weight="400" fill="{colors["sub"]}"> days</tspan></text>',
        f'<text x="0" y="68" fill="{colors["sub"]}" class="sub">{escape(streak_sub)}</text>',
        '</g>',
        # Col 2
        '<g transform="translate(222, 18)">',
        f'<text x="0" y="11" fill="{colors["muted"]}" class="lbl">TOTAL CONTRIBUTIONS</text>',
        f'<text x="0" y="47" fill="{colors["text"]}" font-size="34" font-weight="700" letter-spacing="-0.03em">{total_contribs:,}<tspan font-size="14" font-weight="400" fill="{colors["sub"]}"> total</tspan></text>',
        f'<text x="0" y="68" fill="{colors["sub"]}" class="sub">Past 12 months · ~{rate}/day</text>',
        '</g>',
        # Col 3
        '<g transform="translate(412, 18)">',
        f'<text x="0" y="11" fill="{colors["muted"]}" class="lbl">RECENT CADENCE</text>',
        '</g>',
    ]

    base_x = 412
    base_y = 66
    slot_h = 20
    slot_w = 6.5
    step = 12
    max_c = max(1, max(d.count for d in recent_14)) if recent_14 else 1

    for i, d in enumerate(recent_14):
        bx = base_x + i * step
        is_today = (i == len(recent_14) - 1)
        body.append(f'<rect x="{bx}" y="{base_y - slot_h}" width="{slot_w}" height="{slot_h}" rx="1.5" fill="{colors["track"]}"/>')
        if d.count > 0:
            norm = math.sqrt(d.count) / math.sqrt(max_c)
            bh = max(3, round(norm * slot_h))
            by = base_y - bh
            fill_c = colors["today"] if is_today else colors["spark_fill"]
            body.append(f'<rect x="{bx}" y="{by}" width="{slot_w}" height="{bh}" rx="1.5" fill="{fill_c}"><title>{d.date}: {d.count} commits</title></rect>')

    body.append(f'<g transform="translate(412, 18)"><text x="0" y="68" fill="{colors["sub"]}" class="sub">{active_in_14}/14 days active · {pace_pct}% pace</text></g>')

    desc = f'Current streak: {curr_streak} days. Total {total_contribs} contributions past year. {active_in_14}/14 recent days active.'
    return svg_document(width, height, f'{USERNAME}: coding cadence & velocity', desc, ''.join(body))


def warn(message: str) -> None:
    print(f'::warning::{message}')
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write(f'- {message}\n')


def refresh_trophy(theme: str) -> None:
    errors: list[str] = []
    params = urllib.parse.urlencode({'username': USERNAME, 'title': 'Commits,Repositories,Stars',
                                    'column': 3, 'row': 1, 'theme': 'flat' if theme == 'light' else 'gitdimmed',
                                    'no-frame': 'true', 'no-bg': 'true'})
    for host in TROPHY_HOSTS:
        try:
            data = fetch(host + '?' + params)
            validate_svg(data, ('Commits', 'Repositories', 'Stars'))
            # One-shot trophy progress animation respects reduced-motion preferences.
            data = data.replace(b'</svg>', b'<style>@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></svg>')
            write_svg(STATS / f'trophy-{theme}.svg', data)
            print(f'Refreshed {theme} trophies from {host}')
            return
        except Exception as error:
            errors.append(f'{host}: {error}')
    raise ValueError('; '.join(errors))


def main() -> None:
    today = datetime.now(timezone.utc).date()
    failed = False
    try:
        parser = CalendarParser()
        parser.feed(fetch(f'https://github.com/users/{USERNAME}/contributions').decode('utf-8'))
        days = parser.days(today)
        for theme in THEMES:
            write_svg(STATS / f'activity-{theme}.svg', activity_svg(days, theme))
            write_svg(STATS / f'contributions-{theme}.svg', calendar_svg(days, theme))
            write_svg(STATS / f'streak-{theme}.svg', streak_svg(days, theme, today))
        print(f'Refreshed calendar, activity, and streak through {today}')
    except Exception as error:
        warn(f'Activity/streak refresh failed; previous images were preserved. {error}')
        failed = True
    for theme in THEMES:
        try:
            refresh_trophy(theme)
        except Exception as error:
            warn(f'{theme} trophy refresh failed; previous image was preserved. {error}')
            failed = True
    # No outage is allowed to silently turn an image into an HTML/error response.
    for stem in ('activity', 'contributions', 'trophy', 'streak'):
        for theme in THEMES:
            path = STATS / f'{stem}-{theme}.svg'
            if not path.exists():
                raise RuntimeError(f'No valid image is available: {path.name}')
            validate_svg(path.read_bytes())
    if not failed:
        print('All profile widgets refreshed successfully.')


if __name__ == '__main__':
    main()
