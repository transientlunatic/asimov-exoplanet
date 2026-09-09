"""
Reporting: a self-contained interactive HTML report for one BLS
transit-search result (``build_target_report``), and an aggregate
candidate report across a catalog-scale campaign (``build_catalog_report``).
"""

import json

import numpy as np

#: Cap on the number of light-curve points embedded in the report. A single
#: Kepler quarter of long-cadence data is a few thousand points (fine to
#: embed directly); this just guards against an unusually large light curve
#: (e.g. short cadence, or several stitched quarters in a future phase)
#: blowing up the report's file size. Points are subsampled evenly, not
#: filtered, so the overall shape of the light curve is preserved.
MAX_EMBEDDED_POINTS = 20000


def _subsample(*arrays, max_points=MAX_EMBEDDED_POINTS):
    n = len(arrays[0])
    if n <= max_points:
        return arrays
    indices = np.linspace(0, n - 1, max_points).astype(int)
    return tuple(a[indices] for a in arrays)


def _fold(times, period, epoch):
    """Fold times to phase in [-0.5, 0.5), as a fraction of the period."""
    return (((times - epoch + 0.5 * period) % period) - 0.5 * period) / period


def build_target_report(light_curve, search_result, vetting_result, output_path, target_info=None):
    """
    Build a self-contained interactive HTML report for one target.

    Parameters
    ----------
    light_curve : lightkurve.LightCurve
        The (detrended) light curve BLS was run on.
    search_result : dict
        A result dict as returned by ``asimov_exoplanet.photometry.search``.
    vetting_result : dict
        A result dict as returned by ``asimov_exoplanet.vetting.vet``.
    output_path : str
        Where to write the HTML report.
    target_info : dict, optional
        ``catalog_id``/``mission`` (or similar) to show in the report
        header. Purely cosmetic.

    Returns
    -------
    str
        ``output_path``, for convenience.
    """
    times = light_curve.time.value
    flux = light_curve.flux.value if hasattr(light_curve.flux, "value") else np.asarray(light_curve.flux)
    finite = np.isfinite(times) & np.isfinite(flux)
    times, flux = times[finite], flux[finite]

    period = search_result["period"]
    epoch = search_result["epoch"]
    duration = search_result["duration"]
    depth = search_result["depth"]

    primary_phase = _fold(times, period, epoch)
    secondary_phase = _fold(times, period, epoch - 0.5 * period)
    cycle = np.round((times - epoch) / period).astype(int)
    is_odd = (cycle % 2) != 0

    plot_times, plot_flux, plot_primary_phase, plot_secondary_phase, plot_is_odd = _subsample(
        times, flux, primary_phase, secondary_phase, is_odd
    )

    points = [
        {
            "t": float(t),
            "flux": float(f),
            "phase": float(p),
            "secondaryPhase": float(sp),
            "odd": bool(o),
        }
        for t, f, p, sp, o in zip(
            plot_times, plot_flux, plot_primary_phase, plot_secondary_phase, plot_is_odd
        )
    ]

    half_width_phase = (duration / period) / 2

    data = {
        "target": target_info or {},
        "result": {
            "period": period,
            "epoch": epoch,
            "duration": duration,
            "depth": depth,
            "sde": search_result.get("sde"),
            "halfWidthPhase": half_width_phase,
        },
        "vetting": vetting_result,
        "points": points,
    }

    # target_info comes from blueprint metadata, so treat it as untrusted:
    # escape "</" so a mission/catalog_id string containing "</script>"
    # can't break out of the <script> tag it's embedded in.
    embedded_json = json.dumps(data).replace("</", "<\\/")
    html = _TEMPLATE.replace("__DATA_JSON__", embedded_json)

    with open(output_path, "w") as f:
        f.write(html)

    return output_path


def build_catalog_report(results_by_target, output_path):
    """
    Build an aggregate candidate report across a catalog-scale campaign.

    Parameters
    ----------
    results_by_target : dict
        Mapping of subject name to its ``results.json``-style dict (as
        produced by ``asimov_exoplanet.cli.run``): ``period``, ``epoch``,
        ``duration``, ``depth``, ``sde``, ``vetting_flags``.
    output_path : str
        Where to write the HTML report.

    Returns
    -------
    str
        ``output_path``, for convenience.

    Notes
    -----
    This is a candidate table plus a simple period/depth population
    overview -- deliberately not the "completeness plots for
    injection-recovery studies" DESIGN.md also sketches for Phase 3. Those
    need known injected truth values per target to compare recovered
    parameters against, which this plugin doesn't track; left for a later
    phase.
    """
    candidates = []
    for name in sorted(results_by_target):
        result = results_by_target[name]
        candidates.append(
            {
                "name": name,
                "period": result.get("period"),
                "depth": result.get("depth"),
                "sde": result.get("sde"),
                "flags": result.get("vetting_flags", []),
            }
        )

    data = {
        "candidates": candidates,
        "summary": {
            "total": len(candidates),
            "flagged": sum(1 for c in candidates if c["flags"]),
        },
    }

    # See build_target_report for why "</" is escaped here.
    embedded_json = json.dumps(data).replace("</", "<\\/")
    html = _CATALOG_TEMPLATE.replace("__DATA_JSON__", embedded_json)

    with open(output_path, "w") as f:
        f.write(html)

    return output_path


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit search report</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<style>
  :root {
    --bg: #ffffff;
    --panel: #f7f8fa;
    --border: #e2e5e9;
    --text: #1a1d23;
    --muted: #6b7280;
    --accent: #2563eb;
    --accent-2: #f59e0b;
    --model: #dc2626;
    --ok: #059669;
    --warn: #dc2626;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 32px;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .subtitle { color: var(--muted); margin: 0 0 24px; }
  .layout { display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }
  .stats {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    min-width: 220px;
  }
  .stats dl { margin: 0; display: grid; grid-template-columns: auto auto; gap: 4px 16px; }
  .stats dt { color: var(--muted); }
  .stats dd { margin: 0; font-variant-numeric: tabular-nums; text-align: right; }
  .flags { margin-top: 16px; }
  .flag {
    background: #fef2f2;
    border: 1px solid #fecaca;
    color: var(--warn);
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 12.5px;
    margin-bottom: 6px;
  }
  .flag.ok { background: #ecfdf5; border-color: #a7f3d0; color: var(--ok); }
  .plots { flex: 1; min-width: 480px; display: flex; flex-direction: column; gap: 24px; }
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
  }
  .panel h2 { font-size: 13px; margin: 0 0 8px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  .legend { display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .swatch { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
  .axis text { fill: var(--muted); font-size: 11px; }
  .axis path, .axis line { stroke: var(--border); }
  .tooltip {
    position: absolute;
    pointer-events: none;
    background: var(--text);
    color: white;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 11.5px;
    opacity: 0;
    transition: opacity 0.1s;
  }
</style>
</head>
<body>
<h1 id="title">Transit search report</h1>
<p class="subtitle" id="subtitle"></p>
<div class="layout">
  <div class="stats">
    <dl id="stats-list"></dl>
    <div class="flags" id="flags"></div>
  </div>
  <div class="plots">
    <div class="panel">
      <h2>Folded light curve</h2>
      <div class="legend">
        <span><span class="swatch" style="background:var(--accent)"></span>odd cycle</span>
        <span><span class="swatch" style="background:var(--accent-2)"></span>even cycle</span>
        <span><span class="swatch" style="background:var(--model);width:14px;height:2px;border-radius:0"></span>BLS box model</span>
      </div>
      <svg id="primary-plot"></svg>
    </div>
    <div class="panel">
      <h2>Secondary-eclipse search (phase 0.5)</h2>
      <svg id="secondary-plot"></svg>
    </div>
  </div>
</div>
<div class="tooltip" id="tooltip"></div>
<script>
const data = __DATA_JSON__;

document.getElementById("title").textContent =
  "Transit search: " + (data.target.catalog_id ? (data.target.mission || "") + " " + data.target.catalog_id : "target");
document.getElementById("subtitle").textContent =
  "Period " + data.result.period.toFixed(5) + " d, depth " + (data.result.depth * 1e6).toFixed(0) + " ppm, SDE " +
  (data.result.sde !== null && data.result.sde !== undefined ? data.result.sde.toFixed(1) : "n/a");

const statPairs = [
  ["Period (d)", data.result.period.toFixed(6)],
  ["Epoch", data.result.epoch.toFixed(4)],
  ["Duration (d)", data.result.duration.toFixed(4)],
  ["Depth (ppm)", (data.result.depth * 1e6).toFixed(1)],
  ["SDE", data.result.sde !== null && data.result.sde !== undefined ? data.result.sde.toFixed(2) : "n/a"],
];
const statsList = d3.select("#stats-list");
statPairs.forEach(([k, v]) => {
  statsList.append("dt").text(k);
  statsList.append("dd").text(v);
});

const flagsDiv = d3.select("#flags");
if (data.vetting.flags.length === 0) {
  flagsDiv.append("div").attr("class", "flag ok").text("No vetting flags raised");
} else {
  data.vetting.flags.forEach(flag => {
    flagsDiv.append("div").attr("class", "flag").text(flag);
  });
}

const tooltip = d3.select("#tooltip");

function scatterPlot(selector, xAccessor, xDomain, xLabel) {
  const width = 640, height = 320;
  const margin = { top: 12, right: 16, bottom: 36, left: 48 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const svg = d3.select(selector)
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", "100%")
    .attr("height", height);

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  const x = d3.scaleLinear().domain(xDomain).range([0, innerWidth]);
  const fluxExtent = d3.extent(data.points, d => d.flux);
  const pad = (fluxExtent[1] - fluxExtent[0]) * 0.1 || 0.001;
  const y = d3.scaleLinear()
    .domain([fluxExtent[0] - pad, fluxExtent[1] + pad])
    .range([innerHeight, 0]);

  g.append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${innerHeight})`)
    .call(d3.axisBottom(x).ticks(8));
  g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(6));

  g.append("text")
    .attr("x", innerWidth / 2).attr("y", innerHeight + 32)
    .attr("text-anchor", "middle").attr("fill", "var(--muted)").attr("font-size", 11)
    .text(xLabel);

  g.selectAll("circle")
    .data(data.points)
    .join("circle")
    .attr("cx", d => x(xAccessor(d)))
    .attr("cy", d => y(d.flux))
    .attr("r", 2.2)
    .attr("fill", d => d.odd ? "var(--accent)" : "var(--accent-2)")
    .attr("opacity", 0.65)
    .on("mouseover", (event, d) => {
      tooltip.style("opacity", 1)
        .html(`phase ${xAccessor(d).toFixed(4)}<br>flux ${d.flux.toFixed(5)}`);
    })
    .on("mousemove", (event) => {
      tooltip.style("left", (event.pageX + 12) + "px").style("top", (event.pageY - 24) + "px");
    })
    .on("mouseout", () => tooltip.style("opacity", 0));

  return { g, x, y, innerHeight };
}

const primary = scatterPlot("#primary-plot", d => d.phase, [-0.5, 0.5], "phase (cycles)");
const hw = data.result.halfWidthPhase;
const modelY = 1 - data.result.depth;
primary.g.append("path")
  .attr("d", () => {
    const x = primary.x, y = primary.y;
    return [
      `M${x(-0.5)},${y(1)}`,
      `L${x(-hw)},${y(1)}`,
      `L${x(-hw)},${y(modelY)}`,
      `L${x(hw)},${y(modelY)}`,
      `L${x(hw)},${y(1)}`,
      `L${x(0.5)},${y(1)}`,
    ].join(" ");
  })
  .attr("fill", "none")
  .attr("stroke", "var(--model)")
  .attr("stroke-width", 2);

scatterPlot("#secondary-plot", d => d.secondaryPhase, [-0.5, 0.5], "phase from secondary window (cycles)");
</script>
</body>
</html>
"""


_CATALOG_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catalog transit-search report</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<style>
  :root {
    --bg: #ffffff;
    --panel: #f7f8fa;
    --border: #e2e5e9;
    --text: #1a1d23;
    --muted: #6b7280;
    --accent: #2563eb;
    --warn: #dc2626;
    --ok: #059669;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 32px;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .subtitle { color: var(--muted); margin: 0 0 24px; }
  .layout { display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
  }
  .panel h2 { font-size: 13px; margin: 0 0 8px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { cursor: pointer; user-select: none; color: var(--muted); font-weight: 600; white-space: nowrap; }
  th:hover { color: var(--text); }
  th.sorted::after { content: " \\25BE"; }
  td.numeric, th.numeric { text-align: right; font-variant-numeric: tabular-nums; }
  tr:hover td { background: #eef2ff; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .flag-badge {
    display: inline-block;
    background: #fef2f2;
    color: var(--warn);
    border: 1px solid #fecaca;
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 11px;
  }
  .ok-badge {
    display: inline-block;
    background: #ecfdf5;
    color: var(--ok);
    border: 1px solid #a7f3d0;
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 11px;
  }
  .axis text { fill: var(--muted); font-size: 11px; }
  .axis path, .axis line { stroke: var(--border); }
  .tooltip {
    position: absolute;
    pointer-events: none;
    background: var(--text);
    color: white;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 11.5px;
    opacity: 0;
    transition: opacity 0.1s;
  }
  .table-wrap { max-height: 520px; overflow-y: auto; }
</style>
</head>
<body>
<h1>Catalog transit-search report</h1>
<p class="subtitle" id="subtitle"></p>
<div class="layout">
  <div class="panel" style="flex: 1; min-width: 420px;">
    <h2>Candidates</h2>
    <div class="table-wrap">
      <table id="candidate-table">
        <thead>
          <tr>
            <th data-key="name">Target</th>
            <th data-key="period" class="numeric">Period (d)</th>
            <th data-key="depth" class="numeric">Depth (ppm)</th>
            <th data-key="sde" class="numeric">SDE</th>
            <th data-key="flags">Vetting</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
  <div class="panel" style="flex: 1; min-width: 420px;">
    <h2>Period vs. SDE</h2>
    <svg id="overview-plot"></svg>
  </div>
</div>
<div class="tooltip" id="tooltip"></div>
<script>
const data = __DATA_JSON__;

document.getElementById("subtitle").textContent =
  data.summary.total + " target" + (data.summary.total === 1 ? "" : "s") +
  ", " + data.summary.flagged + " flagged by vetting";

function fmt(value, digits) {
  return (value === null || value === undefined) ? "n/a" : value.toFixed(digits);
}

let sortKey = "sde";
let sortDescending = true;

function renderTable() {
  const sorted = [...data.candidates].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string") return sortDescending ? bv.localeCompare(av) : av.localeCompare(bv);
    return sortDescending ? bv - av : av - bv;
  });

  d3.selectAll("#candidate-table th").classed("sorted", false);
  d3.select(`#candidate-table th[data-key="${sortKey}"]`).classed("sorted", true);

  const rows = d3.select("#candidate-table tbody")
    .selectAll("tr")
    .data(sorted, d => d.name)
    .join("tr")
    .order();

  // Subject names (and, in principle, vetting flag text) come from
  // blueprint metadata -- an untrusted source -- so build each cell with
  // .text()/.attr() rather than interpolating them into an .html() string;
  // otherwise a name like "<img onerror=...>" would execute as markup.
  rows.each(function (d) {
    const tr = d3.select(this).html("");

    tr.append("td").append("a")
      .attr("href", `${encodeURIComponent(d.name)}/folded_lightcurve.html`)
      .text(d.name);

    tr.append("td").attr("class", "numeric").text(fmt(d.period, 5));
    tr.append("td").attr("class", "numeric")
      .text(d.depth === null || d.depth === undefined ? "n/a" : (d.depth * 1e6).toFixed(1));
    tr.append("td").attr("class", "numeric").text(fmt(d.sde, 2));

    const flagsCell = tr.append("td");
    if (d.flags.length === 0) {
      flagsCell.append("span").attr("class", "ok-badge").text("clean");
    } else {
      flagsCell.append("span")
        .attr("class", "flag-badge")
        .attr("title", d.flags.join("; "))
        .text(`${d.flags.length} flag${d.flags.length === 1 ? "" : "s"}`);
    }
  });
}

d3.selectAll("#candidate-table th").on("click", function () {
  const key = d3.select(this).attr("data-key");
  if (key === sortKey) {
    sortDescending = !sortDescending;
  } else {
    sortKey = key;
    sortDescending = true;
  }
  renderTable();
});

renderTable();

const tooltip = d3.select("#tooltip");
const width = 480, height = 360;
const margin = { top: 12, right: 16, bottom: 36, left: 48 };
const innerWidth = width - margin.left - margin.right;
const innerHeight = height - margin.top - margin.bottom;

const svg = d3.select("#overview-plot")
  .attr("viewBox", `0 0 ${width} ${height}`)
  .attr("width", "100%")
  .attr("height", height);
const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

const plottable = data.candidates.filter(
  d => d.period !== null && d.period !== undefined && d.period > 0 && d.sde !== null && d.sde !== undefined
);
// d3.extent() returns [undefined, undefined] on an empty array, which
// scaleLog().domain() throws on -- fall back to a placeholder domain so an
// empty (or all-missing-period) catalog still renders empty axes instead
// of leaving the whole report broken.
const periodExtent = d3.extent(plottable, d => d.period);
const x = d3.scaleLog().domain(periodExtent[0] === undefined ? [0.1, 100] : periodExtent).nice().range([0, innerWidth]);
const y = d3.scaleLinear().domain([0, d3.max(plottable, d => d.sde) || 1]).nice().range([innerHeight, 0]);

g.append("g").attr("class", "axis").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(x).ticks(5, "~g"));
g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(6));
g.append("text").attr("x", innerWidth / 2).attr("y", innerHeight + 32).attr("text-anchor", "middle")
  .attr("fill", "var(--muted)").attr("font-size", 11).text("period (d, log scale)");
g.append("text").attr("transform", "rotate(-90)").attr("x", -innerHeight / 2).attr("y", -34)
  .attr("text-anchor", "middle").attr("fill", "var(--muted)").attr("font-size", 11).text("SDE");

g.selectAll("circle")
  .data(plottable)
  .join("circle")
  .attr("cx", d => x(d.period))
  .attr("cy", d => y(d.sde))
  .attr("r", 4)
  .attr("fill", d => d.flags.length === 0 ? "var(--accent)" : "var(--warn)")
  .attr("opacity", 0.75)
  .on("mouseover", (event, d) => {
    tooltip.style("opacity", 1).html("");
    tooltip.append("strong").text(d.name);
    tooltip.append("br");
    tooltip.append(() => document.createTextNode(`period ${fmt(d.period, 4)} d`));
    tooltip.append("br");
    tooltip.append(() => document.createTextNode(`SDE ${fmt(d.sde, 2)}`));
  })
  .on("mousemove", (event) => {
    tooltip.style("left", (event.pageX + 12) + "px").style("top", (event.pageY - 24) + "px");
  })
  .on("mouseout", () => tooltip.style("opacity", 0));
</script>
</body>
</html>
"""
