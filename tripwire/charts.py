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


def short_number(value):
    """12345678 -> '12.3M', 4500 -> '4.5K'."""
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            text = f"{value / size:.1f}".rstrip("0").rstrip(".")
            return text + suffix
    return f"{value:g}"


def line_chart(labels, series, width=640, height=260, y_suffix="", y_max=None, x_ticks=6, band=None, markers=(),
               series_offset=0, y_min=0):
    """A line chart with one y axis. labels: the x values (e.g. years);
    series: [(key, label, values)] in a fixed order (colours follow that order,
    starting after `series_offset`, so a series keeps its colour across charts).
    y_min: where the y axis starts (0 unless the values are all far from zero).
    A value of None means "no data here", so a line can start part-way along.
    band: optional {"series": n, "low": [...], "high": [...], "label"} shaded range.
    markers: optional [{"index", "value", "label"}] highlighted points.
    Returns everything a template needs to draw it as SVG."""
    left, right, top, bottom = 52, 16, 12, 28
    plot_w, plot_h = width - left - right, height - top - bottom
    everything = [v for _, _, values in series for v in values if v is not None]
    if band:
        everything += [v for v in band["high"] if v is not None]
    everything += [m["value"] for m in markers]
    top_value = y_max or nice_max(max(everything, default=1))
    if y_min:
        top_value = y_min + nice_max(top_value - y_min)
    count = len(labels)

    def x(i):
        return left + (plot_w * i / (count - 1) if count > 1 else plot_w / 2)

    def y(v):
        return top + plot_h - plot_h * (v - y_min) / (top_value - y_min)

    def path(values):
        parts, pen_down = [], False
        for i, v in enumerate(values):
            if v is None:
                pen_down = False
                continue
            parts.append(f"{'L' if pen_down else 'M'}{x(i):.1f},{y(v):.1f}")
            pen_down = True
        return " ".join(parts)

    lines = []
    for n, (key, label, values) in enumerate(series, start=1 + series_offset):
        known = [v for v in values if v is not None]
        lines.append({"key": key, "label": label, "series": n, "path": path(values),
                      "last": known[-1] if known else None})
    band_out = None
    if band:
        pairs = [(i, lo, hi) for i, (lo, hi) in enumerate(zip(band["low"], band["high"])) if lo is not None]
        if pairs:
            outline = [f"{x(i):.1f},{y(hi):.1f}" for i, _, hi in pairs] + [f"{x(i):.1f},{y(lo):.1f}" for i, lo, _ in reversed(pairs)]
            band_out = {"points": " ".join(outline), "series": band["series"], "label": band["label"],
                        "low": pairs[-1][1], "high": pairs[-1][2]}
    markers_out = [{"x": x(m["index"]), "y": y(m["value"]), "label": m["label"]} for m in markers]
    step = max(1, round((count - 1) / max(1, x_ticks - 1))) if count > 1 else 1
    x_ticks_out = [{"x": x(i), "label": labels[i]} for i in range(0, count, step)]
    y_ticks = [{"y": y(y_min + (top_value - y_min) * f), "label": short_number(y_min + (top_value - y_min) * f) + y_suffix}
               for f in (0, .25, .5, .75, 1)]
    # One invisible column per x value, for hover tooltips listing every series' value.
    columns = [{"x": x(i) - (plot_w / max(count - 1, 1)) / 2, "w": plot_w / max(count - 1, 1), "label": labels[i],
                "readings": [(label, values[i]) for _, label, values in series if values[i] is not None]}
               for i in range(count)]
    return {"width": width, "height": height, "left": left, "top": top, "plot_w": plot_w, "plot_h": plot_h,
            "base": top + plot_h, "lines": lines, "x_ticks": x_ticks_out, "y_ticks": y_ticks,
            "columns": columns, "y_suffix": y_suffix, "band": band_out, "markers": markers_out}
