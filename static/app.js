(function () {
  "use strict";

  var PROJ4 = "+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs";
  var VIRIDIS =
    "linear-gradient(to right, #440154, #482878, #3e4989, #31688e, #26828e, " +
    "#1f9e89, #35b779, #6ece58, #b5de2b, #fde725)";
  var GREY = "linear-gradient(to right, #000000, #ffffff)";
  var PRESENT_KEY = "anuma.presentation";
  var RAIL_KEY = "anuma.rail";
  // URL switches for demos and screenshots: ?present=1&rail=0&site=1
  var PARAMS = new URLSearchParams(window.location.search);

  var map = null;
  var overlay = null;
  var siteLayer = null;
  var artemisLayer = null;
  var meta = null;
  var lastScore = null;
  var overlayUrl = null;
  var selectedRank = null;
  var currentSites = null;
  var currentRadius = 0;
  // Monte Carlo top-10 frequency by "row:col", and the Stage B 20 m checks.
  var stability = null;
  var stabilityById = {};
  var checks = null;
  // Stage D DEM-noise verdicts, matched by exact "row:col".
  var noiseChecks = null;
  // Cited Artemis III points from /artemis3.geojson, fetched once.
  var artemisGeojson = null;
  // Only the newest /score and layer request may paint; older replies are dropped.
  var scoreSeq = 0;
  var layerSeq = 0;
  // Colour range of the last display-only layer PNG, from its response headers.
  var layerRange = null;
  var initialSiteShown = false;
  var MAX_MONTHS = 600;
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  var els = {
    illum: document.getElementById("w-illum"),
    hydro: document.getElementById("w-hydro"),
    earth: document.getElementById("w-earth"),
    illumVal: document.getElementById("w-illum-val"),
    hydroVal: document.getElementById("w-hydro-val"),
    earthVal: document.getElementById("w-earth-val"),
    slope: document.getElementById("max-slope"),
    slopeRadius: document.getElementById("slope-radius"),
    score: document.getElementById("btn-score"),
    csv: document.getElementById("btn-csv"),
    geojson: document.getElementById("btn-geojson"),
    layer: document.getElementById("layer-select"),
    artemis: document.getElementById("artemis-toggle"),
    sites: document.getElementById("sites"),
    veil: document.getElementById("veil"),
    veilKicker: document.getElementById("veil-kicker"),
    veilBody: document.getElementById("veil-body"),
    veilCmd: document.getElementById("veil-cmd"),
    veilNote: document.getElementById("veil-note"),
    coords: document.getElementById("coords"),
    scaleLine: document.getElementById("scale-line"),
    scaleLabel: document.getElementById("scale-label"),
    artemisRail: document.getElementById("artemis-rail"),
    artemisNote: document.getElementById("artemis-note"),
    artemisList: document.getElementById("artemis-list"),
    zoomIn: document.getElementById("zoom-in"),
    zoomOut: document.getElementById("zoom-out"),
    status: document.getElementById("status"),
    legendTitle: document.getElementById("legend-title"),
    legendBar: document.getElementById("legend-bar"),
    legendLow: document.getElementById("legend-low"),
    legendHigh: document.getElementById("legend-high"),
    legendNote: document.getElementById("legend-note"),
    tableNote: document.getElementById("table-note"),
    infoButton: document.getElementById("info-button"),
    railToggle: document.getElementById("rail-toggle"),
    present: document.getElementById("present-toggle"),
    keepout: document.getElementById("keepout-toggle"),
    keepoutBody: document.getElementById("keepout-body"),
    keepoutLists: document.getElementById("keepout-lists"),
    keepoutRadius: document.getElementById("keepout-radius"),
    keepoutMode: document.getElementById("keepout-mode"),
    keepoutPenalty: document.getElementById("keepout-penalty"),
    keepoutPenaltyRow: document.getElementById("keepout-penalty-row"),
    keepoutNote: document.getElementById("keepout-note"),
    keepoutError: document.getElementById("keepout-error"),
    windowStart: document.getElementById("window-start"),
    windowEnd: document.getElementById("window-end"),
    windowNote: document.getElementById("window-note"),
    windowReset: document.getElementById("window-reset"),
  };

  function fmt(n) {
    return Number(n).toFixed(2);
  }

  function fmtLat(lat) {
    return Math.abs(lat).toFixed(4) + "°" + (lat <= 0 ? "S" : "N");
  }

  function fmtLon(lon) {
    var wrapped = ((lon + 180) % 360 + 360) % 360 - 180;
    var hemi = wrapped < 0 ? "W" : "E";
    return Math.abs(wrapped).toFixed(4) + "°" + hemi;
  }

  // "89.45°S 137.3°W": 0.01° of latitude and 0.1° of longitude are both
  // a few hundred metres this close to the pole.
  function fmtLocation(lat, lon) {
    var wrapped = ((lon + 180) % 360 + 360) % 360 - 180;
    return (
      Math.abs(lat).toFixed(2) + "°" + (lat <= 0 ? "S" : "N") + " " +
      Math.abs(wrapped).toFixed(1) + "°" + (wrapped < 0 ? "W" : "E")
    );
  }

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function debounce(fn, ms) {
    var timer = null;
    return function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(fn, ms);
    };
  }

  function showVeil(kicker, body, command, note) {
    els.veil.hidden = false;
    els.veilKicker.textContent = kicker;
    els.veilBody.textContent = body;
    if (command) {
      els.veilCmd.hidden = false;
      els.veilCmd.textContent = command;
    } else {
      els.veilCmd.hidden = true;
      els.veilCmd.textContent = "";
    }
    els.veilNote.textContent = note || "";
  }

  function hideVeil() {
    els.veil.hidden = true;
  }

  function columnCount() {
    return document.querySelectorAll(".sites-table thead th").length;
  }

  function emptySites(message) {
    els.sites.innerHTML =
      '<tr><td class="empty" colspan="' + columnCount() + '">' + message + "</td></tr>";
  }

  function showNotIngested(command) {
    showVeil(
      "Grid not on disk",
      "anuma will not invent a south-polar score. Warp the LOLA DEM, illumination map, and hydrogen layer onto the 240 m grid first.",
      command || "python3 -m anuma.ingest",
      "The DEM is 2.51 GiB. If ingest has not finished, the map stays empty."
    );
    emptySites("No ranked sites until the grid is ingested and scored.");
  }

  function projectedBounds(bounds) {
    return L.bounds(
      L.point(bounds[0], bounds[1]),
      L.point(bounds[2], bounds[3])
    );
  }

  function buildCrs(info) {
    var xmin = info.bounds[0];
    var ymin = info.bounds[1];
    var xmax = info.bounds[2];
    var ymax = info.bounds[3];
    var extent = Math.max(xmax - xmin, ymax - ymin);
    var resolutions = [];
    var z;
    for (z = 0; z < 9; z += 1) {
      resolutions.push(extent / 256 / Math.pow(2, z));
    }
    return new L.Proj.CRS("ANUMA:SPOLE", PROJ4, {
      origin: [xmin, ymax],
      bounds: L.bounds([xmin, ymin], [xmax, ymax]),
      resolutions: resolutions,
    });
  }

  function layerLabel(name) {
    return name === "dem" ? "DEM" : name.charAt(0).toUpperCase() + name.slice(1).replace(/_/g, " ");
  }

  // Score is viridis; single layers are grey, normalised to 0–1 when the
  // layer has a scoring mode, so black is the worse end.
  function updateLegend(name) {
    if (name === "score") {
      els.legendTitle.textContent = "Combined score";
      els.legendBar.style.background = VIRIDIS;
      els.legendLow.textContent = "low";
      els.legendHigh.textContent = "high";
      els.legendNote.textContent = "blank = fails slope rule";
      return;
    }
    var info = ((meta && meta.layers) || []).filter(function (layer) {
      return layer.name === name;
    })[0] || {};
    if (info.gate) {
      // Stage F hard-gate layer: share of flat 20 m ground within 1 km, 0–100 %.
      els.legendTitle.textContent = info.label || layerLabel(name);
      els.legendBar.style.background = GREY;
      els.legendLow.textContent = "0 %";
      els.legendHigh.textContent = "100 %";
      var gate = (meta && meta.terrain_gate) || {};
      els.legendNote.textContent =
        "hard rule, not scored: cells below " +
        Math.round(100 * (gate.min_fraction == null ? 0.5 : gate.min_fraction)) +
        " % are excluded; blank = no data";
      return;
    }
    if (info.display_only) {
      // Raw values on a fixed range in metres; not normalised, not scored.
      var u = info.unit ? " " + info.unit : "";
      els.legendTitle.textContent = info.label || layerLabel(name);
      els.legendBar.style.background = GREY;
      if (layerRange) {
        els.legendLow.textContent = fmtRange(layerRange.min) + u;
        els.legendHigh.textContent =
          (layerRange.pct ? "≥ " : "") + fmtRange(layerRange.max) + u;
      } else {
        els.legendLow.textContent = "low";
        els.legendHigh.textContent = "high";
      }
      // The PNG draws the largest error in each block; the range is over those pixels.
      els.legendNote.textContent =
        "display only, not scored; black = smallest error, white = largest error" +
        (layerRange && layerRange.pct
          ? " (≥ p" + layerRange.pct + " of shown pixels, each its block maximum)"
          : "") +
        "; blank = no data";
      return;
    }
    var unit = info.unit ? " (" + info.unit + ")" : "";
    var months = name === "illumination" ? currentWindow() : null;
    els.legendTitle.textContent =
      layerLabel(name) + unit + (months ? " · " + windowLabel(months) : "");
    els.legendBar.style.background = GREY;
    if (info.mode === "lower_better") {
      els.legendLow.textContent = "high";
      els.legendHigh.textContent = "low";
    } else {
      els.legendLow.textContent = "low";
      els.legendHigh.textContent = "high";
    }
    if (name === "slope") {
      els.legendNote.textContent = "normalised; white = flattest, blank = fails slope rule";
    } else if (info.mode) {
      els.legendNote.textContent = "normalised; black = worse, blank = no data";
    } else {
      els.legendNote.textContent = "blank = no data";
    }
  }

  function fmtRange(value) {
    return value >= 10 ? value.toFixed(0) : value.toFixed(1);
  }

  function setOverlay(url, bounds, name) {
    if (overlay) {
      map.removeLayer(overlay);
      overlay = null;
    }
    if (overlayUrl && overlayUrl.indexOf("blob:") === 0) {
      URL.revokeObjectURL(overlayUrl);
    }
    overlayUrl = url;
    overlay = L.Proj.imageOverlay(url, projectedBounds(bounds), {
      opacity: 0.92,
      pane: "overlayPane",
    });
    overlay.addTo(map);
    updateLegend(name || "score");
  }

  function addPoleMarker() {
    L.marker([-90, 0], {
      icon: L.divIcon({
        className: "pole-mark",
        html: '<span class="pole-ring"></span>',
        iconSize: [12, 12],
        iconAnchor: [6, 6],
      }),
      interactive: false,
      keyboard: false,
    }).addTo(map);
  }

  function updateScale() {
    if (!map) {
      return;
    }
    var size = map.getSize();
    var y = size.y / 2;
    var a = map.containerPointToLatLng([0, y]);
    var b = map.containerPointToLatLng([80, y]);
    var pa = proj4(PROJ4, [a.lng, a.lat]);
    var pb = proj4(PROJ4, [b.lng, b.lat]);
    var metres = Math.hypot(pb[0] - pa[0], pb[1] - pa[1]);
    var targets = [5, 10, 20, 25, 50, 100, 200];
    var i;
    var km80 = metres / 1000;
    var chosen = targets[0];
    for (i = 0; i < targets.length; i += 1) {
      if (Math.abs(targets[i] - km80) < Math.abs(chosen - km80)) {
        chosen = targets[i];
      }
    }
    var px = (chosen * 80) / km80;
    els.scaleLine.style.width = Math.max(24, Math.min(140, px)) + "px";
    els.scaleLabel.textContent = chosen + " km";
  }

  function updateCoords(latlng) {
    if (!latlng) {
      return;
    }
    els.coords.textContent = fmtLat(latlng.lat) + "  " + fmtLon(latlng.lng);
  }

  // Score split into its three parts; the full bar width is a score of 1.
  function scoreBar(contrib) {
    var parts = [
      ["illumination", "c-sun"],
      ["hydrogen", "c-hyd"],
      ["earth_visibility", "c-earth"],
    ];
    var html = parts
      .map(function (part) {
        var value = Math.max(0, Number(contrib[part[0]]) || 0);
        return '<i class="' + part[1] + '" style="width:' + (100 * Math.min(1, value)).toFixed(1) + '%"></i>';
      })
      .join("");
    return '<span class="score-bar" aria-hidden="true">' + html + "</span>";
  }

  function contribCell(value) {
    return value == null ? "–" : Number(value).toFixed(2);
  }

  function siteKey(site) {
    return site.row + ":" + site.col;
  }

  function winnerFrequency(site) {
    var winners = (stability && stability.winners) || [];
    var i;
    for (i = 0; i < winners.length; i += 1) {
      if (winners[i].row === site.row && winners[i].col === site.col) {
        return winners[i].frequency;
      }
    }
    return null;
  }

  // Verdict mark: shape + colour, with the word for screen readers and hover.
  var VERDICT = {
    pass: { mark: "✓", cls: "v-pass", word: "passes" },
    fail: { mark: "✗", cls: "v-fail", word: "fails" },
    edge: { mark: "≈", cls: "v-edge", word: "borderline" },
    none: { mark: "–", cls: "v-none", word: "not checked" },
  };

  function verdictHtml(kind, extra, title) {
    var v = VERDICT[kind];
    var label = escapeHtml(title || v.word);
    return (
      '<span class="v ' + v.cls + '" title="' + label + '" aria-label="' + label + '">' +
      v.mark + "</span>" + (extra ? " " + extra : "")
    );
  }

  function stabilityRow(site) {
    return stabilityById[siteKey(site)] || null;
  }

  function stabilityCell(site) {
    if (!stability || !stability.available) {
      return verdictHtml("none", "", "Monte Carlo weight study not on this machine");
    }
    var row = stabilityRow(site);
    // Every site that reached the top 10 in any draw is listed, so a
    // missing site was in the top 10 in none of the draws.
    var freq = row ? row.frequency : 0;
    return (100 * freq).toFixed(freq === 1 || freq === 0 ? 0 : 1) + " %";
  }

  // 20 m flat-ground check for the same 240 m cell only, so a neighbouring
  // cell never inherits a check.
  function stageBCheck(site) {
    if (!checks || !checks.sites) {
      return null;
    }
    var found = null;
    checks.sites.forEach(function (check) {
      if (check.row === site.row && check.col === site.col) {
        found = check;
      }
    });
    return found;
  }

  function flatThreshold() {
    return (noiseChecks && noiseChecks.threshold) || 0.5;
  }

  // Share of 20 m pixels at or below 8° within 1 km, or null if unchecked.
  function flatFraction(site) {
    var check = stageBCheck(site);
    return check ? check.fraction_le_8deg.within_1km : null;
  }

  function flatCell(site) {
    var value = flatFraction(site);
    if (value == null) {
      return verdictHtml("none", "", "not in the 20 m terrain check");
    }
    var pct = Math.round(100 * value) + " %";
    var pass = value >= flatThreshold();
    return verdictHtml(
      pass ? "pass" : "fail",
      pct,
      (pass ? "passes" : "fails") + ": " + pct + " of 20 m pixels within 1 km are ≤ 8°"
    );
  }

  // DEM-noise verdict for the same 240 m cell only.
  function noiseCheck(site) {
    var found = null;
    ((noiseChecks && noiseChecks.sites) || []).forEach(function (check) {
      if (check.row === site.row && check.col === site.col) {
        found = check;
      }
    });
    return found;
  }

  // passes / fails in every draw; otherwise borderline (on the 50 % line,
  // or a mix of passing and failing draws).
  function noiseKind(found) {
    if (!found) {
      return "none";
    }
    if (found.noise_unstable) {
      return "edge";
    }
    if (found.pass_rate === 1) {
      return "pass";
    }
    if (found.pass_rate === 0) {
      return "fail";
    }
    return "edge";
  }

  function noiseWords(found) {
    if (!found) {
      return "not checked";
    }
    var kind = noiseKind(found);
    var pass = (100 * found.pass_rate).toFixed(0) + " % of " + noiseChecks.draws + " draws pass";
    if (kind === "pass") {
      return "passes in every one of " + noiseChecks.draws + " DEM-noise draws";
    }
    if (kind === "fail") {
      return "fails in every one of " + noiseChecks.draws + " DEM-noise draws";
    }
    return "borderline: " + pass + (found.noise_unstable ? "; sits on the 50 % line" : "");
  }

  function noiseCell(site) {
    var found = noiseCheck(site);
    return verdictHtml(noiseKind(found), "", noiseWords(found));
  }

  function renderTableNote() {
    var parts = [];
    parts.push(
      "<p><b>Score</b> 0–1: weighted sum of normalised sunlight, hydrogen and Earth view. " +
      "Sites must pass the slope rule on the 240 m grid. Ranked sites are at least 10 km apart.</p>"
    );
    if (stability && stability.available) {
      var text =
        "<p><b>Weight-robust</b>: share of " + stability.draw_count +
        " random weight sets (each weight varied by ±0.1, job " + escapeHtml(stability.job_id) +
        ") in which the site stays in the top 10. Measured for the default scenario only.";
      var winner = (stability.winners || [])[0];
      if (winner) {
        text += " The default #1 is the winner in " + (100 * winner.frequency).toFixed(1) + " % of draws.";
      }
      parts.push(text + "</p>");
    } else {
      parts.push("<p><b>Weight-robust</b>: Monte Carlo weight study not on this machine.</p>");
    }
    if (checks) {
      parts.push(
        "<p><b>Flat ground 1 km</b>: share of 20 m LOLA pixels at or below 8° within 1 km " +
        "of the 240 m cell centre (job " + escapeHtml(checks.source.job) +
        "). Only the default top 10 were checked.</p>"
      );
    }
    if (noiseChecks) {
      parts.push(
        "<p><b>DEM-noise</b>: the flat-ground rule (≥ 50 % within 1 km) re-run in " +
        noiseChecks.draws + " draws of the published LOLA height error (job " +
        escapeHtml(noiseChecks.source.job) + "). ✓/✗ = same answer in every draw; " +
        "≈ = the answer flips or sits on the 50 % line. Only 8 sites were tested.</p>"
      );
    }
    els.tableNote.innerHTML = parts.join("");
  }

  function isPresenting() {
    return document.body.classList.contains("present");
  }

  function labelSize() {
    return isPresenting() ? { w: 34, h: 24 } : { w: 26, h: 18 };
  }

  function overlaps(a, b) {
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  }

  // Put each rank label next to its dot, trying rings of offsets in rank
  // order so a label does not cover another label or another site's dot.
  function placeLabels(sites) {
    var size = labelSize();
    var gapY = size.h / 2 + 5;
    var gapX = size.w / 2 + 6;
    var directions = [[0, -1], [0, 1], [1, 0], [-1, 0], [1, -1], [-1, -1], [1, 1], [-1, 1]];
    var candidates = [];
    var ring;
    for (ring = 1; ring <= 4; ring += 1) {
      directions.forEach(function (d) {
        candidates.push([d[0] * gapX * ring, d[1] * gapY * ring]);
      });
    }
    var points = sites.map(function (site) {
      return map.latLngToLayerPoint([site.lat, site.lon]);
    });
    var dots = points.map(function (pt) {
      return { x: pt.x - 4, y: pt.y - 4, w: 8, h: 8 };
    });
    var placed = [];
    return points.map(function (pt, index) {
      var best = null;
      var bestHits = Infinity;
      candidates.some(function (offset) {
        var box = {
          x: pt.x + offset[0] - size.w / 2,
          y: pt.y + offset[1] - size.h / 2,
          w: size.w,
          h: size.h,
        };
        var hits = placed.concat(dots.filter(function (_, j) {
          return j !== index;
        })).filter(function (other) {
          return overlaps(box, other);
        }).length;
        if (hits < bestHits) {
          bestHits = hits;
          best = { offset: offset, box: box };
        }
        return hits === 0;
      });
      placed.push(best.box);
      return best.offset;
    });
  }

  function selectSite(index) {
    var site = currentSites[index];
    selectedRank = index;
    map.setView([site.lat, site.lon], Math.max(map.getZoom(), 4));
    renderSites(currentSites, currentRadius);
  }

  function drawMarkers() {
    if (siteLayer) {
      map.removeLayer(siteLayer);
      siteLayer = null;
    }
    if (!currentSites || !currentSites.length) {
      return;
    }
    siteLayer = L.layerGroup().addTo(map);
    var size = labelSize();
    var offsets = placeLabels(currentSites);
    currentSites.forEach(function (site, index) {
      if (selectedRank === index && currentRadius > 0) {
        L.polygon(radiusRing(site, currentRadius), {
          color: "#e60019",
          weight: 1.5,
          fillOpacity: 0.08,
          interactive: false,
        }).addTo(siteLayer);
      }
      var left = offsets[index][0] - size.w / 2;
      var top = offsets[index][1] - size.h / 2;
      L.marker([site.lat, site.lon], {
        icon: L.divIcon({
          className: "site-mark" + (selectedRank === index ? " active" : ""),
          html:
            '<svg class="site-leader" width="1" height="1"><line x1="0" y1="0" x2="' +
            offsets[index][0] + '" y2="' + offsets[index][1] + '"></line></svg>' +
            '<span class="site-dot"></span><span class="site-label" style="left:' +
            left + "px;top:" + top + "px;width:" + size.w + "px;height:" + size.h +
            "px;line-height:" + size.h + 'px">' + pad2(index + 1) + "</span>",
          iconSize: [0, 0],
          iconAnchor: [0, 0],
        }),
        zIndexOffset: 1000 - index,
      })
        .on("click", function () {
          selectSite(index);
        })
        .addTo(siteLayer);
    });
  }

  function artemisCell(site) {
    if (site.nearest) {
      var dist =
        site.distance_km == null
          ? ""
          : ' <span class="a-dist">' + Number(site.distance_km).toFixed(1) + " km</span>";
      return '<span class="a-name">' + escapeHtml(site.nearest) + "</span>" + dist;
    }
    return '<span class="na">none cited</span>';
  }

  // L.circle measures with Earth's radius under Proj4Leaflet, so the ring is
  // traced in the grid's stereographic metres instead.
  function radiusRing(site, radiusM) {
    var projection = map.options.crs.projection;
    var centre = projection.project(L.latLng(site.lat, site.lon));
    var ring = [];
    var i;
    for (i = 0; i < 64; i += 1) {
      var angle = (2 * Math.PI * i) / 64;
      ring.push(
        projection.unproject(
          L.point(
            centre.x + radiusM * Math.cos(angle),
            centre.y + radiusM * Math.sin(angle)
          )
        )
      );
    }
    return ring;
  }

  function renderSites(sites, slopeRadius) {
    els.sites.innerHTML = "";
    currentSites = sites || [];
    currentRadius = slopeRadius || 0;
    renderTableNote();
    if (!currentSites.length) {
      drawMarkers();
      emptySites("No sites passed the slope mask.");
      return;
    }
    currentSites.forEach(function (site, index) {
      var contrib = site.contributions || {};
      var tr = document.createElement("tr");
      if (selectedRank === index) {
        tr.className = "active";
      }
      tr.innerHTML =
        '<td class="num rank">' +
        pad2(index + 1) +
        '</td><td class="loc">' +
        fmtLocation(site.lat, site.lon) +
        '</td><td class="num score-cell">' +
        Number(site.total).toFixed(3) +
        scoreBar(contrib) +
        '</td><td class="num contrib">' +
        contribCell(contrib.illumination) +
        '</td><td class="num contrib">' +
        contribCell(contrib.hydrogen) +
        '</td><td class="num contrib">' +
        contribCell(contrib.earth_visibility) +
        '</td><td class="num">' +
        stabilityCell(site) +
        '</td><td class="num">' +
        flatCell(site) +
        "</td><td>" +
        noiseCell(site) +
        '</td><td class="wrap">' +
        artemisCell(site) +
        "</td>";
      tr.addEventListener("click", function () {
        selectSite(index);
      });
      els.sites.appendChild(tr);
    });
    drawMarkers();
  }

  // Artemis III regions ranked by the score at their approximate point; the
  // list sits in a closed <details> so it never pushes the Score button away.
  function renderArtemis(artemis) {
    if (!artemis) {
      els.artemisRail.hidden = true;
      return;
    }
    els.artemisRail.hidden = false;
    els.artemisNote.textContent =
      "Score at each region's approximate point (Goossens et al. 2025), best first. " +
      "NASA has not published region centres.";
    var regions = (artemis.regions || []).slice().sort(function (a, b) {
      var ra = a.rank == null ? Infinity : a.rank;
      var rb = b.rank == null ? Infinity : b.rank;
      return ra - rb || String(a.name).localeCompare(String(b.name));
    });
    els.artemisList.innerHTML = "";
    regions.forEach(function (region) {
      var li = document.createElement("li");
      var why =
        region.coordinate_status === "not_published"
          ? "no cited point"
          : "fails slope rule or keep-out at its point";
      li.innerHTML =
        escapeHtml(region.name) +
        (region.best_score == null
          ? ' <span class="na">' + why + "</span>"
          : ' <span class="mono">' + Number(region.best_score).toFixed(2) + "</span>");
      if (region.rank == null) {
        li.className = "unranked";
      }
      els.artemisList.appendChild(li);
    });
  }

  function currentWeights() {
    return {
      illumination: Number(els.illum.value),
      hydrogen: Number(els.hydro.value),
      earth_visibility: Number(els.earth.value),
    };
  }

  // ---- Artemis III keep-out

  function keepoutLists() {
    return els.keepoutLists.value.split(",");
  }

  // Cited points on the chosen lists. Regions without a published point are
  // counted so the note can say how many were skipped.
  function keepoutPoints() {
    var lists = keepoutLists();
    var points = [];
    var skipped = 0;
    ((artemisGeojson && artemisGeojson.features) || []).forEach(function (feat) {
      var props = feat.properties || {};
      var onList = (props.lists || []).some(function (year) {
        return lists.indexOf(year) >= 0;
      });
      if (!onList) {
        return;
      }
      if (!feat.geometry) {
        skipped += 1;
        return;
      }
      points.push({ lon: feat.geometry.coordinates[0], lat: feat.geometry.coordinates[1] });
    });
    return { points: points, skipped: skipped };
  }

  function updateKeepoutNote() {
    els.keepoutBody.hidden = !els.keepout.checked;
    els.keepoutPenaltyRow.hidden = els.keepoutMode.value !== "penalty";
    var label = els.keepoutLists.options[els.keepoutLists.selectedIndex].text;
    if (!artemisGeojson) {
      els.keepoutNote.textContent = "Artemis III points not loaded.";
      return;
    }
    var used = keepoutPoints();
    els.keepoutNote.textContent =
      used.points.length + " approximate points from the " + label +
      " (Goossens et al. 2025, not NASA centres)" +
      (used.skipped ? "; " + used.skipped + " regions have no cited point and are not avoided." : ".");
  }

  // Blank or non-positive radius or penalty: say so next to the field and
  // send nothing, rather than raising the map veil for a typo.
  function keepoutError() {
    if (!els.keepout.checked) {
      return "";
    }
    var radius = els.keepoutRadius.value === "" ? NaN : Number(els.keepoutRadius.value);
    if (!(radius > 0)) {
      return "Enter a radius above 0 km.";
    }
    if (els.keepoutMode.value === "penalty") {
      var penalty = els.keepoutPenalty.value === "" ? NaN : Number(els.keepoutPenalty.value);
      if (!(penalty > 0)) {
        return "Enter a penalty above 0.";
      }
    }
    return "";
  }

  function showKeepoutError() {
    var message = keepoutError();
    els.keepoutError.textContent = message;
    els.keepoutError.hidden = !message;
    return message;
  }

  function currentKeepout() {
    if (!els.keepout.checked) {
      return null;
    }
    var spec = {
      points: keepoutPoints().points,
      radius_km: Number(els.keepoutRadius.value),
      mode: els.keepoutMode.value,
    };
    if (spec.mode === "penalty") {
      spec.penalty = Number(els.keepoutPenalty.value);
    }
    return spec;
  }

  async function loadArtemisPoints() {
    try {
      var response = await fetch("/artemis3.geojson");
      artemisGeojson = response.ok ? await response.json() : null;
    } catch (err) {
      artemisGeojson = null;
    }
    updateKeepoutNote();
  }

  // ---- Stage E month window

  function monthText(value) {
    var parts = value.split("-");
    return MONTHS[Number(parts[1]) - 1] + " " + parts[0];
  }

  function windowLabel(months) {
    return months.start === months.end
      ? monthText(months.start)
      : monthText(months.start) + " – " + monthText(months.end);
  }

  function currentWindow() {
    if (!els.windowStart.value || !els.windowEnd.value) {
      return null;
    }
    return { start: els.windowStart.value, end: els.windowEnd.value };
  }

  // Months since year 0, or NaN when the text is not YYYY-MM.
  function monthIndex(text) {
    var match = /^([0-9]{4})-([0-9]{2})$/.exec(String(text || ""));
    if (!match || Number(match[2]) < 1 || Number(match[2]) > 12) {
      return NaN;
    }
    return Number(match[1]) * 12 + Number(match[2]) - 1;
  }

  function fillWindowSelects(info) {
    var spec = info.illumination_window;
    if (!spec) {
      return;
    }
    var html = '<option value="">average</option>';
    var first = monthIndex(spec.first_month);
    var count = Math.min(monthIndex(spec.last_month) - first + 1, MAX_MONTHS);
    var i;
    // A malformed range gives NaN or a count below 1, so no months are offered.
    for (i = 0; count > 0 && i < count; i += 1) {
      var value = String(Math.floor((first + i) / 12)).padStart(4, "0") + "-" + pad2(((first + i) % 12) + 1);
      html += '<option value="' + value + '">' + monthText(value) + "</option>";
    }
    els.windowStart.innerHTML = html;
    els.windowEnd.innerHTML = html;
    els.windowReset.textContent = spec.default_label;
    if (!spec.available || !(count > 0)) {
      els.windowStart.disabled = true;
      els.windowEnd.disabled = true;
    }
    updateWindowNote();
  }

  function updateWindowNote() {
    var spec = (meta && meta.illumination_window) || {};
    var months = currentWindow();
    var note = (spec.note || "Scenario window, not mission dates") + ". ";
    if (spec.available === false) {
      note += "Daily sunlight data not available on this machine.";
    } else if (months) {
      note += "Using Stage E daily sunlight, " + windowLabel(months) + ".";
    } else {
      note += "Using the " + (spec.default_label || "published map") + ".";
    }
    els.windowNote.textContent = note;
    // Disabled while the published average is already in use.
    els.windowReset.disabled = !months;
  }

  // Keep start <= end, then re-score once both months are set.
  function onWindowChange(changed) {
    var start = els.windowStart.value;
    var end = els.windowEnd.value;
    if (changed === "start" && start && (!end || end < start)) {
      els.windowEnd.value = start;
    } else if (changed === "end" && end && (!start || start > end)) {
      els.windowStart.value = end;
    } else if (!start || !end) {
      els.windowStart.value = "";
      els.windowEnd.value = "";
    }
    updateWindowNote();
    scheduleScore();
  }

  function resetWindow() {
    els.windowStart.value = "";
    els.windowEnd.value = "";
    updateWindowNote();
    scheduleScore();
  }

  // Select and number changes re-score once they settle.
  var scheduleScore = debounce(function () {
    runScore({ keepLayer: true });
  }, 300);

  function currentRequest() {
    var request = {
      weights: currentWeights(),
      hard: {
        max_slope_deg: Number(els.slope.value),
        slope_radius_m: Number(els.slopeRadius.value),
      },
      n: (meta.defaults && meta.defaults.n) || 10,
    };
    var keepout = currentKeepout();
    if (keepout) {
      request.keepout = keepout;
    }
    var months = currentWindow();
    if (months) {
      request.illumination_months = months;
    }
    return request;
  }

  async function downloadRanking(path, filename) {
    if (showKeepoutError()) {
      return;
    }
    var response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentRequest()),
    });
    if (!response.ok) {
      throw new Error("Export failed (HTTP " + response.status + ")");
    }
    var blob = await response.blob();
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function applyDefaults(info) {
    var weights = (info.defaults && info.defaults.weights) || {};
    if (weights.illumination != null) {
      els.illum.value = weights.illumination;
    }
    if (weights.hydrogen != null) {
      els.hydro.value = weights.hydrogen;
    }
    if (weights.earth_visibility != null) {
      els.earth.value = weights.earth_visibility;
    }
    if (info.defaults && info.defaults.max_slope_deg != null) {
      els.slope.value = info.defaults.max_slope_deg;
    }
    if (info.defaults && info.defaults.slope_radius_m != null) {
      els.slopeRadius.value = info.defaults.slope_radius_m;
    }
    syncSliderLabels();
  }

  function syncSliderLabels() {
    els.illumVal.textContent = fmt(els.illum.value);
    els.hydroVal.textContent = fmt(els.hydro.value);
    els.earthVal.textContent = fmt(els.earth.value);
  }

  function fillLayerSelect(info) {
    var keep = '<option value="score">Combined score</option>';
    (info.layers || []).forEach(function (layer) {
      var label = escapeHtml(
        layer.label || (layer.name === "dem" ? "DEM" : layer.name.replace(/_/g, " "))
      );
      var value = escapeHtml(layer.name);
      if ((layer.display_only || layer.gate) && !layer.available) {
        // Optional layer not built on this machine: listed, but not selectable.
        keep += '<option value="' + value + '" disabled>' + label + " (not available)</option>";
        return;
      }
      keep += '<option value="' + value + '">' + label + "</option>";
    });
    els.layer.innerHTML = keep;
  }

  function initMap(info) {
    var crs = buildCrs(info);
    map = L.map("map", {
      crs: crs,
      center: [-90, 0],
      zoom: 2,
      minZoom: 0,
      maxZoom: 8,
      zoomControl: false,
      attributionControl: false,
      boxZoom: false,
    });
    var sw = crs.projection.unproject(L.point(info.bounds[0], info.bounds[1]));
    var ne = crs.projection.unproject(L.point(info.bounds[2], info.bounds[3]));
    map.fitBounds(L.latLngBounds(sw, ne), { animate: false, padding: [4, 4] });
    addPoleMarker();
    map.on("mousemove", function (event) {
      updateCoords(event.latlng);
    });
    map.on("zoomend moveend", updateScale);
    map.on("zoomend", drawMarkers);
    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        map.invalidateSize();
        updateScale();
      }).observe(document.getElementById("map-frame"));
    }
    window.setTimeout(function () {
      map.invalidateSize();
      updateScale();
    }, 80);
    els.zoomIn.addEventListener("click", function () {
      map.zoomIn();
    });
    els.zoomOut.addEventListener("click", function () {
      map.zoomOut();
    });
  }

  function detailFrom(response, payload) {
    if (payload && payload.detail && typeof payload.detail === "object") {
      return payload.detail;
    }
    return payload || { message: "HTTP " + response.status };
  }

  // keepLayer: a re-score from the keep-out or window controls leaves a
  // single layer on the map and refreshes it instead of switching to score.
  async function runScore(options) {
    var keepLayer = options && options.keepLayer && els.layer.value !== "score";
    if (showKeepoutError()) {
      return;
    }
    var seq = ++scoreSeq;
    els.score.disabled = true;
    els.csv.disabled = true;
    els.geojson.disabled = true;
    els.score.textContent = "Scoring";
    try {
      var request = currentRequest();
      var response = await fetch("/score", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      var payload = await response.json();
      if (seq !== scoreSeq) {
        return;
      }
      if (response.status === 412) {
        var detail = detailFrom(response, payload);
        showNotIngested(detail.ingest_command);
        return;
      }
      if (!response.ok) {
        var message = Array.isArray(payload.detail)
          ? payload.detail
              .map(function (item) {
                return (item.loc || []).slice(-1)[0] + ": " + item.msg;
              })
              .join("; ")
          : payload.detail;
        showVeil(
          "Score failed",
          message || payload.message || "The API returned an error.",
          "",
          ""
        );
        return;
      }
      lastScore = payload;
      hideVeil();
      if (keepLayer) {
        await showSingleLayer(els.layer.value);
      } else {
        setOverlay("data:image/png;base64," + payload.png, payload.bounds, "score");
        els.layer.value = "score";
      }
      selectedRank = null;
      renderSites(payload.sites, request.hard.slope_radius_m);
      renderArtemis(payload.artemis);
      if (PARAMS.get("site") && !initialSiteShown) {
        initialSiteShown = true;
        var wanted = Number(PARAMS.get("site")) - 1;
        if (wanted >= 0 && wanted < currentSites.length) {
          selectSite(wanted);
        }
      }
      els.csv.disabled = false;
      els.geojson.disabled = false;
    } catch (err) {
      if (seq === scoreSeq) {
        showVeil("Score failed", String(err), "", "");
      }
    } finally {
      if (seq === scoreSeq) {
        els.score.disabled = false;
        els.score.textContent = "Score";
      }
    }
  }

  async function showSingleLayer(name) {
    var seq = ++layerSeq;
    if (name === "score") {
      if (lastScore) {
        hideVeil();
        setOverlay("data:image/png;base64," + lastScore.png, lastScore.bounds, "score");
      } else if (meta && !meta.ingested) {
        showNotIngested(meta.ingest_command);
      } else {
        showVeil(
          "No score yet",
          "Set illumination, hydrogen, and Earth-visibility weights, then Score.",
          "",
          "Viridis is the science colormap. Single layers render in grey."
        );
      }
      return;
    }
    try {
      var path = "/layer/" + encodeURIComponent(name) + ".png";
      var months = name === "illumination" ? currentWindow() : null;
      if (months) {
        path += "?start=" + encodeURIComponent(months.start) + "&end=" + encodeURIComponent(months.end);
      }
      var response = await fetch(path);
      if (seq !== layerSeq) {
        return;
      }
      if (response.status === 412) {
        var payload = await response.json();
        showNotIngested(detailFrom(response, payload).ingest_command);
        return;
      }
      if (!response.ok) {
        var failure = await response.json().catch(function () {
          return {};
        });
        showVeil(
          "Layer missing",
          (typeof failure.detail === "string" && failure.detail) || "Could not load " + name + ".",
          "",
          ""
        );
        return;
      }
      var blob = await response.blob();
      if (seq !== layerSeq) {
        return;
      }
      layerRange = null;
      var low = response.headers.get("X-Anuma-Range-Min");
      var high = response.headers.get("X-Anuma-Range-Max");
      if (low !== null && high !== null) {
        layerRange = {
          min: Number(low),
          max: Number(high),
          pct: response.headers.get("X-Anuma-Range-Max-Percentile"),
        };
      }
      var url = URL.createObjectURL(blob);
      hideVeil();
      setOverlay(url, meta.bounds, name);
    } catch (err) {
      if (seq === layerSeq) {
        showVeil("Layer missing", String(err), "", "");
      }
    }
  }

  async function toggleArtemis() {
    if (!els.artemis.checked) {
      if (artemisLayer) {
        map.removeLayer(artemisLayer);
        artemisLayer = null;
      }
      return;
    }
    var geojson = artemisGeojson;
    if (!geojson) {
      var response = await fetch("/artemis3.geojson");
      geojson = await response.json();
    }
    if (artemisLayer) {
      map.removeLayer(artemisLayer);
    }
    artemisLayer = L.geoJSON(geojson, {
      style: {
        color: "#161921",
        weight: 1,
        fill: false,
      },
    }).addTo(map);
  }

  // Stability and the 20 m check only annotate the table; failures leave it plain.
  async function loadAnnotations() {
    try {
      var response = await fetch("/stability.json");
      stability = response.ok ? await response.json() : null;
    } catch (err) {
      stability = null;
    }
    stabilityById = {};
    if (stability && stability.available) {
      (stability.top10_membership_frequency || []).forEach(function (row) {
        stabilityById[row.row + ":" + row.col] = row;
      });
    }
    try {
      var checkResponse = await fetch("/checks/stage-b-1km.json");
      checks = checkResponse.ok ? await checkResponse.json() : null;
    } catch (err) {
      checks = null;
    }
    try {
      var noiseResponse = await fetch("/checks/stage-d-noise.json");
      noiseChecks = noiseResponse.ok ? await noiseResponse.json() : null;
    } catch (err) {
      noiseChecks = null;
    }
  }

  function setPresenting(on) {
    // The page sizes in rem, so the root element carries the class too.
    document.documentElement.classList.toggle("present", on);
    document.body.classList.toggle("present", on);
    els.present.setAttribute("aria-pressed", on ? "true" : "false");
    try {
      window.localStorage.setItem(PRESENT_KEY, on ? "1" : "0");
    } catch (err) {
      // Storage can be blocked; the toggle still works for this page.
    }
    if (map) {
      map.invalidateSize();
      drawMarkers();
    }
  }

  function setRail(open) {
    document.body.classList.toggle("rail-closed", !open);
    els.railToggle.setAttribute("aria-expanded", open ? "true" : "false");
    els.railToggle.textContent = open ? "Scenario ◂" : "Scenario ▸";
    if (map) {
      window.setTimeout(function () {
        map.invalidateSize();
        drawMarkers();
      }, 0);
    }
  }

  function railOpen() {
    return !document.body.classList.contains("rail-closed");
  }

  function setInfo(open) {
    els.tableNote.hidden = !open;
    els.infoButton.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function storedPresenting() {
    if (PARAMS.has("present")) {
      return PARAMS.get("present") === "1";
    }
    try {
      return window.localStorage.getItem(PRESENT_KEY) === "1";
    } catch (err) {
      return false;
    }
  }

  async function main() {
    setPresenting(storedPresenting());
    // Presentation mode starts with the scenario rail closed.
    setRail(PARAMS.has("rail") ? PARAMS.get("rail") === "1" : !isPresenting());
    els.present.addEventListener("click", function () {
      setPresenting(!isPresenting());
      setRail(!isPresenting());
    });
    els.railToggle.addEventListener("click", function () {
      setRail(!railOpen());
    });
    els.infoButton.addEventListener("click", function (event) {
      event.stopPropagation();
      setInfo(els.tableNote.hidden);
    });
    document.addEventListener("click", function (event) {
      if (!els.tableNote.hidden && !els.tableNote.contains(event.target)) {
        setInfo(false);
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        setInfo(false);
        if (isPresenting() && railOpen()) {
          setRail(false);
        }
      }
    });
    [els.illum, els.hydro, els.earth].forEach(function (slider) {
      slider.addEventListener("input", syncSliderLabels);
    });
    els.score.addEventListener("click", function () {
      runScore();
    });
    els.csv.addEventListener("click", function () {
      downloadRanking("/score.csv", "anuma-ranked-sites.csv").catch(function (err) {
        showVeil("Export failed", String(err), "", "");
      });
    });
    els.geojson.addEventListener("click", function () {
      downloadRanking("/score.geojson", "anuma-ranked-sites.geojson").catch(function (err) {
        showVeil("Export failed", String(err), "", "");
      });
    });
    els.layer.addEventListener("change", function () {
      showSingleLayer(els.layer.value);
    });
    els.artemis.addEventListener("change", toggleArtemis);
    els.keepout.addEventListener("change", function () {
      updateKeepoutNote();
      showKeepoutError();
      scheduleScore();
    });
    [els.keepoutLists, els.keepoutRadius, els.keepoutMode, els.keepoutPenalty].forEach(function (input) {
      ["input", "change"].forEach(function (type) {
        input.addEventListener(type, function () {
          updateKeepoutNote();
          if (els.keepout.checked && !showKeepoutError()) {
            scheduleScore();
          }
        });
      });
    });
    els.windowStart.addEventListener("change", function () {
      onWindowChange("start");
    });
    els.windowEnd.addEventListener("change", function () {
      onWindowChange("end");
    });
    els.windowReset.addEventListener("click", resetWindow);

    try {
      var response = await fetch("/layers");
      meta = await response.json();
    } catch (err) {
      showVeil("API unreachable", String(err), "", "");
      return;
    }

    applyDefaults(meta);
    fillLayerSelect(meta);
    fillWindowSelects(meta);
    initMap(meta);
    await loadAnnotations();
    await loadArtemisPoints();

    if (!meta.ingested) {
      showNotIngested(meta.ingest_command);
    } else {
      await runScore();
    }
  }

  main();
})();
