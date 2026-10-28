"""Small charts drawn as SVG on the server, so pages need no chart library.

Colours come from CSS variables (--series-1 ... --series-6 in style.css), in a
fixed order that was checked to stay tellable-apart for colourblind readers,
in both light and dark mode. Each chart always shows its numbers in text too.
"""

import math


def donut(counts, labels):
    """Slices for a donut chart showing parts of a whole.

    counts: {key: number}, labels: {key: label}, both in the same fixed order.
    Returns {"total", "slices": [{key, label, count, percent, series, dash, offset}]}.
    Slices use the stroke-dasharray technique on a circle whose circumference is 100,
    with a small gap between slices so neighbouring colours never touch.
    """
    total = sum(counts.values())
    slices, start = [], 0.0
    for index, (key, label) in enumerate(labels.items(), start=1):
        count = counts.get(key, 0)
        percent = 100 * count / total if total else 0
        gap = 0.8 if 0 < percent < 100 else 0
        slices.append({"key": key, "label": label, "count": count, "percent": percent,
                       "percent_text": f"{percent:.0f}%" if percent >= 1 or not count else "<1%",
                       "series": index, "dash": max(percent - gap, 0), "offset": 25 - start})
        start += percent
    return {"total": total, "slices": slices}


def nice_max(value):
    """Round an axis maximum up to a tidy number (1, 2, 2.5, 5 × 10ⁿ)."""
    if value <= 0:
        return 1
    power = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * power:
            return step * power
    return 10 * power
