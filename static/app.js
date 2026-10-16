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

  // Hand-placed offline labels: approximate crater centres (deg, east positive).
  var PLACES = [
    { name: "Shackleton", lat: -89.67, lon: 129.78, nearPole: true },
    { name: "de Gerlache", lat: -88.5, lon: -87.1 },
    { name: "Shoemaker", lat: -88.1, lon: 44.9 },
    { name: "Haworth", lat: -87.45, lon: -5.0 },
    { name: "Faustini", lat: -87.3, lon: 77.0 },
    { name: "Nobile", lat: -85.2, lon: 53.5 },
    { name: "Malapert (crater)", lat: -84.9, lon: 12.9 },
    { name: "Amundsen", lat: -84.5, lon: 82.8 },
  ];

  var map = null;
  var baseLayer = null;
  var overlay = null;
  var siteLayer = null;
  var artemisLayer = null;
  var meta = null;
  var lastScore = null;
  var overlayUrl = null;
  var selectedRank = null;
  var currentSites = null;
  var currentRadius = 0;
  // Monte Carlo top-10 frequency by "row:col", and the 20 m flat-ground checks.
  var stability = null;
  var stabilityById = {};
  var checks = null;
  // DEM-noise verdicts, matched by exact "row:col".
  var noiseChecks = null;
  // Cited Artemis III points from /artemis3.geojson, fetched once.
  var artemisGeojson = null;
  // Only the newest /score and layer request may paint; older replies are dropped.
  var scoreSeq = 0;
  var layerSeq = 0;
  // Colour range of the last display-only layer PNG, from its response headers.
  var layerRange = null;
  // Raw layer values from /site, by "row:col|window".
  var siteValueCache = {};
  // The request behind the table on screen, and how it differs from the defaults.
  var lastRequest = null;
  var lastKeepoutLists = null;
  var lastScenario = null;
  // A pinned site card for side-by-side comparison: a snapshot, kept across re-scores.
  var pinned = null;
  var previousRanks = null;
  var keepoutLayer = null;
  var cardsClosed = false;
  var cardsSeq = 0;
  // The user's last setting of the 20 m rule switch, kept while it is disabled.
  var terrainChoice = null;
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
    groupKm: document.getElementById("group-km"),
    score: document.getElementById("btn-score"),
    csv: document.getElementById("btn-csv"),
    geojson: document.getElementById("btn-geojson"),
    link: document.getElementById("btn-link"),
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
    answerMain: document.getElementById("answer-main"),
    answerPass: document.getElementById("answer-pass"),
    onlyPassing: document.getElementById("only-passing"),
    sitesTable: document.querySelector(".sites-table"),
    cards: document.getElementById("cards"),
    cardsEmpty: document.getElementById("cards-empty"),
    mapFrame: document.getElementById("map-frame"),
    sitesPanel: document.querySelector(".panel-sites"),
    answerMeta: document.getElementById("answer-meta"),
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
    terrain: document.getElementById("terrain-toggle"),
    terrainNote: document.getElementById("terrain-note"),
  };

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
      els.legendTitle.textContent = "Combined score (0–1)";
      els.legendBar.style.background = VIRIDIS;
      var range = lastScore && lastScore.score_range;
      els.legendLow.textContent = range ? range[0].toFixed(2) : "low";
      els.legendHigh.textContent = range ? range[1].toFixed(2) : "high";
      els.legendNote.textContent = "no colour (grey terrain) = fails a hard rule (see Why excluded)";
      return;
    }
    var info = ((meta && meta.layers) || []).filter(function (layer) {
      return layer.name === name;
    })[0] || {};
    if (info.gate) {
      // Gate layer: share of flat 20 m ground within 1 km, 0–100 %.
      var gate = (meta && meta.terrain_gate) || {};
      var cut = Math.round(100 * (gate.min_fraction == null ? 0.5 : gate.min_fraction));
      els.legendTitle.textContent = "Flat ground within 1 km at 20 m";
      els.legendBar.style.background = GREY;
      els.legendLow.textContent = "0 %";
      els.legendHigh.textContent = "100 %";
      els.legendNote.textContent =
        "passes at ≥ " + cut + " %; a hard rule, not scored; blank = no data";
      return;
    }
    if (info.excluded) {
      // Category layer: the first default hard rule each cell fails.
      var flatCut = info.flat_ground == null ? null : Math.round(100 * info.flat_ground);
      els.legendTitle.textContent = "Why excluded (default hard rules)";
      els.legendBar.style.background = flatCut == null
        ? "rgb(215, 48, 39)"
        : "linear-gradient(to right, rgb(215, 48, 39) 50%, rgb(255, 214, 0) 50%)";
      els.legendLow.textContent = "too steep";
      els.legendHigh.textContent = flatCut == null ? "" : "too little flat ground";
      els.legendNote.innerHTML =
        '<i class="sw sw-steep"></i>slope above the limit at 240 m' +
        (flatCut == null ? "" : '<i class="sw sw-flat"></i>under ' + flatCut + " % flat 20 m ground within 1 km") +
        "; no colour = passes; keep-out is drawn separately";
      return;
    }
    if (info.darkness) {
      // Stage G days on a fixed grey ramp; never-lit pixels in black.
      var dm = name === "longest_dark" ? currentWindow() : null;
      var period = dm ? windowLabel(dm) : darkPeriod();
      var dark = name === "longest_dark";
      els.legendTitle.textContent =
        (dark ? "Longest continuous darkness" : "Longest continuous sunlight") + " (days), " + period + " modelled";
      els.legendBar.style.background = dark
        ? "linear-gradient(to right, #f2f2f2, #4d4d4d)"
        : "linear-gradient(to right, #4d4d4d, #f2f2f2)";
      els.legendLow.textContent = "0";
      els.legendHigh.textContent = "≥ " + (layerRange ? fmtRange(layerRange.max) : dark ? "45" : "30");
      els.legendNote.innerHTML =
        '<i class="sw sw-never"></i>never lit' + (dm ? " in this window" : "") +
        "; " + (dark ? "white = shortest darkness" : "white = longest sunlight") +
        "; display only, not scored; blank = no data";
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
      // The PNG is the grid pixel for pixel; the range is over those pixels.
      els.legendNote.textContent =
        "display only, not scored; black = smallest error, white = largest error" +
        (layerRange && layerRange.pct ? " (≥ p" + layerRange.pct + " of grid cells)" : "") +
        "; blank = no data";
      return;
    }
    var unit = info.unit ? " (" + info.unit + ")" : "";
    var months = name === "illumination" ? currentWindow() : null;
    els.legendTitle.textContent =
      layerLabel(name) + unit + (months ? " · " + windowLabel(months) : "");
    els.legendBar.style.background = GREY;
    // Normalised layers run 0 (worse) to 1 (better).
    if (info.mode) {
      els.legendLow.textContent = "0 worse";
      els.legendHigh.textContent = "1 better";
    } else {
      els.legendLow.textContent = "low";
      els.legendHigh.textContent = "high";
    }
    if (name === "slope") {
      els.legendNote.textContent = "normalised; white = flattest, no grey = fails slope rule";
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
    // Semi-transparent over the grey terrain, so blank cells show the ground.
    overlay = L.Proj.imageOverlay(url, projectedBounds(bounds), {
      opacity: 0.78,
      pane: "scorePane",
    });
    overlay.addTo(map);
    updateLegend(name || "score");
  }

  // Grey LOLA elevation under the score, so the map reads as terrain.
  async function addTerrain(info) {
    try {
      var response = await fetch("/layer/dem.png");
      if (!response.ok) {
        return;
      }
      var url = URL.createObjectURL(await response.blob());
      baseLayer = L.Proj.imageOverlay(url, projectedBounds(info.bounds), {
        opacity: 0.5,
        pane: "basePane",
        interactive: false,
      }).addTo(map);
    } catch (err) {
      // No terrain backdrop; the score still draws.
    }
  }

  // Circle of constant latitude, traced in the map's stereographic metres.
  function latitudeRing(crs, lat) {
    var radius = Math.abs(crs.projection.project(L.latLng(lat, 0)).y);
    var ring = [];
    var i;
    for (i = 0; i <= 180; i += 1) {
      var angle = (2 * Math.PI * i) / 180;
      ring.push(crs.projection.unproject(L.point(radius * Math.cos(angle), radius * Math.sin(angle))));
    }
    return ring;
  }

  function addGraticule(crs) {
    L.polyline(latitudeRing(crs, -85), {
      color: "#161921",
      weight: 1,
      opacity: 0.55,
      dashArray: "4 4",
      interactive: false,
      pane: "labelPane",
    }).addTo(map);
    placeLabel("85°S", -85, 135, "grat-label");
  }

  function placeLabel(text, lat, lon, cls) {
    L.marker([lat, lon], {
      icon: L.divIcon({
        className: "place-mark",
        html: '<span class="' + cls + '">' + escapeHtml(text) + "</span>",
        iconSize: [0, 0],
        iconAnchor: [0, 0],
      }),
      interactive: false,
      keyboard: false,
      pane: "labelPane",
    }).addTo(map);
  }

  function addPlaceLabels() {
    PLACES.forEach(function (place) {
      placeLabel(place.name, place.lat, place.lon, "place-label" + (place.nearPole ? " near-pole" : ""));
    });
    placeLabel("South Pole", -90, 0, "place-label pole-label near-pole");
    // Below zoom 3 the near-pole labels would sit on the ranked-site labels.
    var syncZoomClass = function () {
      map.getContainer().classList.toggle("zoom-lt3", map.getZoom() < 3);
    };
    map.on("zoomend", syncZoomClass);
    syncZoomClass();
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

  // ---- Stage G darkness (per site, from /score; never scored)

  // Longest dark stretch shown for a site: the month window's when one is
  // active, else the full modelled period. null when the server has no darkness data.
  function darkOf(site, months) {
    if (!site) {
      return null;
    }
    if (months) {
      if (site.window_longest_dark_hours == null) {
        return null;
      }
      return {
        hours: site.window_longest_dark_hours,
        censored: !!site.window_longest_dark_censored,
        never: !!site.window_never_lit,
        months: months,
      };
    }
    if (site.longest_dark_hours == null) {
      return null;
    }
    return {
      hours: site.longest_dark_hours,
      censored: !!site.longest_dark_censored,
      never: !!site.never_lit,
      months: null,
    };
  }

  function daysText(hours) {
    return (hours / 24).toFixed(1);
  }

  function darkWhy(months) {
    var dark = (lastScore && lastScore.darkness) || {};
    if (!dark.available) {
      return dark.reason || "darkness data not on this machine";
    }
    if (months && dark.window && !dark.window.available) {
      return dark.window.reason || "not available for this window";
    }
    return "not available";
  }

  function darkCell(site, months) {
    var d = darkOf(site, months);
    if (!d) {
      return '<span class="na" title="' + escapeHtml(darkWhy(months)) + '">–</span>';
    }
    if (d.never) {
      return '<span class="dark-never" title="no sunlit hour in ' +
        escapeHtml(months ? windowLabel(months) : darkPeriod()) + '">never lit</span>';
    }
    var title = (d.censored ? "touches the edge of the period; may be longer. " : "") +
      d.hours + " h" + (months ? " in " + windowLabel(months) : ", " + darkPeriod());
    return '<span title="' + escapeHtml(title) + '">' + (d.censored ? "≥ " : "") + daysText(d.hours) + '<span class="unit"> d</span></span>';
  }

  var MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // "29 Jan–4 Feb 2032" from the run's first hour and its exclusive end hour
  // (the last dark sample is one hour before the end).
  function spanText(startIso, endIso) {
    var a = new Date(startIso.replace(/Z$/, ":00Z"));
    var b = new Date(new Date(endIso.replace(/Z$/, ":00Z")).getTime() - 3600 * 1000);
    if (isNaN(a.getTime()) || isNaN(b.getTime())) {
      return "";
    }
    var d1 = a.getUTCDate(), m1 = MONTH_SHORT[a.getUTCMonth()], y1 = a.getUTCFullYear();
    var d2 = b.getUTCDate(), m2 = MONTH_SHORT[b.getUTCMonth()], y2 = b.getUTCFullYear();
    if (y1 !== y2) {
      return d1 + " " + m1 + " " + y1 + "–" + d2 + " " + m2 + " " + y2;
    }
    if (m1 !== m2) {
      return d1 + " " + m1 + "–" + d2 + " " + m2 + " " + y2;
    }
    return d1 === d2 ? d1 + " " + m1 + " " + y1 : d1 + "–" + d2 + " " + m1 + " " + y1;
  }

  // The modelled darkness period, from /layers ("2026–2044").
  function darkPeriod() {
    return (meta && meta.darkness && meta.darkness.period_label) || "the modelled period";
  }

  function darkFacts(card) {
    var site = card.site;
    if (site.longest_dark_hours == null) {
      return fact("Longest darkness", '<span class="na">' + escapeHtml(darkWhy(null)) + "</span>");
    }
    var period = darkPeriod();
    var days = site.never_lit
      ? "never lit"
      : (site.longest_dark_censored ? "≥ " : "") + daysText(site.longest_dark_hours);
    var span =
      !site.never_lit && site.longest_dark_start_utc && site.longest_dark_end_utc
        ? " (" + spanText(site.longest_dark_start_utc, site.longest_dark_end_utc) + ")"
        : "";
    var lit = site.longest_lit_hours === 0
      ? "never lit"
      : (site.longest_lit_censored ? "≥ " : "") + (site.longest_lit_hours / 24).toFixed(0);
    var w = card.months ? darkOf(site, card.months) : null;
    var windowText = !card.months
      ? ""
      : !w
        ? escapeHtml(windowLabel(card.months)) + ": " + escapeHtml(darkWhy(card.months))
        : (w.never ? "never lit" : (w.censored ? "≥ " : "") + daysText(w.hours) + " d") +
          " in " + escapeHtml(windowLabel(card.months));
    if (isPresenting()) {
      // One row in presentation mode, so the check rows below still fit.
      var main = card.months
        ? windowText
        : escapeHtml(days + (site.never_lit ? "" : " d") + span);
      return fact(
        "Longest dark / lit",
        main + " / " + escapeHtml(lit + (site.longest_lit_hours === 0 ? "" : " d"))
      );
    }
    var full = site.never_lit
      ? "never lit " + period
      : days + " days" + span;
    var out = "";
    if (card.months) {
      out += fact(
        "Longest darkness",
        (w ? windowText.replace(/ d in /, " days in ") : '<span class="na">' + windowText + "</span>") +
          ' <span class="na">(' + escapeHtml(period) + ": " + escapeHtml(full) + ")</span>"
      );
    } else {
      out += fact("Longest darkness", escapeHtml(full));
    }
    out += fact(
      "Longest sunlight",
      site.longest_lit_hours === 0 ? "never lit" : escapeHtml(lit) + " days"
    );
    return out;
  }

  function darkWords(site, months) {
    var d = darkOf(site, months);
    if (!d) {
      return null;
    }
    var where = months ? " in " + windowLabel(months) : " (modelled " + darkPeriod() + ")";
    if (d.never) {
      return "never lit" + where;
    }
    return "longest dark stretch " + (d.censored ? "≥ " : "") + daysText(d.hours) + " days" + where;
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

  function previousRankHtml(site, index) {
    if (!previousRanks) {
      return "";
    }
    var before = previousRanks[siteKey(site)];
    if (before === index + 1) {
      return "";
    }
    var text = before ? "was " + before : "new";
    return ' <span class="was" title="Rank before the last scenario change">' + text + "</span>";
  }

  function scenarioNow() {
    return lastScenario || { isDefault: true, studiesOk: true, slopeChanged: false, parts: [] };
  }

  // False when /score ranked on a different basis than the studies the
  // server serves (slope-only, or the 20 m terrain gate of Stage H).
  function studiesMatch(payload) {
    var basis = payload && payload.ranking_basis;
    return !(basis && typeof basis === "object" && basis.studies_match === false);
  }

  function studiesNote() {
    var basis = lastScore && lastScore.ranking_basis;
    if (basis && typeof basis === "object" && basis.studies_match === false) {
      return basis.note || "the studies were computed for a different ranking";
    }
    return "the site was not in the study, which used the default scenario";
  }

  // Why the served studies do not describe this ranking, in words from /score.
  function studiesWhy(payload) {
    var basis = payload && payload.ranking_basis;
    return (basis && basis.note) || "the studies were computed for a different ranking";
  }

  function noiseSiteCount() {
    return ((noiseChecks && noiseChecks.sites) || []).length;
  }

  function naHtml() {
    return '<span class="na" title="n/a for this scenario: ' + escapeHtml(studiesNote()) + '">n/a</span>';
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
    if (!studiesMatch(lastScore)) {
      return null;
    }
    return stabilityById[siteKey(site)] || null;
  }

  function stabilityCell(site) {
    if (!stability || !stability.available) {
      return verdictHtml("none", "", "Monte Carlo weight study not on this machine");
    }
    var row = stabilityRow(site);
    var scenario = scenarioNow();
    if (!scenario.studiesOk && !row) {
      return naHtml();
    }
    // Every site that reached the top 10 in any draw is listed, so a
    // missing site was in the top 10 in none of the draws.
    var freq = row ? row.frequency : 0;
    var text = (100 * freq).toFixed(freq === 1 || freq === 0 ? 0 : 1) + " %";
    if (!scenario.studiesOk) {
      return '<span class="na" title="Measured for the default scenario only">' + text + "</span>";
    }
    return text;
  }

  // 20 m flat-ground check for the same 240 m cell only, so a neighbouring
  // cell never inherits a check.
  function stageBCheck(site) {
    if (!checks || !checks.sites || !studiesMatch(lastScore)) {
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

  // The per-site gate-layer value from /score, or null.
  function layerFraction(site) {
    var detail = lastScore && lastScore.terrain_gate_detail;
    if (site.terrain_1km_fraction == null || !detail || !lastRequest) {
      return null;
    }
    // A terrain property at the layer's slope limit, valid whatever the
    // request's max slope; only whether the gate applies depends on that.
    return site.terrain_1km_fraction;
  }

  // Share of 20 m pixels at or below 8° within 1 km, or null if unchecked:
  // the full-cap gate layer for every site, else the study file.
  function flatFraction(site) {
    var fromLayer = layerFraction(site);
    if (fromLayer != null) {
      return fromLayer;
    }
    var check = stageBCheck(site);
    return check ? check.fraction_le_8deg.within_1km : null;
  }

  // ---- "Passes all checks"

  // True when /score says the 20 m flat-ground rule already filtered the
  // ranking, so every ranked site passes it by construction.
  function gateApplied(payload) {
    var gate = payload && payload.terrain_gate;
    if (gate == null) {
      return false;
    }
    if (typeof gate === "string") {
      return gate === "applied";
    }
    if (typeof gate === "boolean") {
      return gate;
    }
    if (gate.status != null) {
      return gate.status === "applied";
    }
    return gate.applied === true;
  }

  // "pass", "fail", or "unchecked" for flat ground plus DEM-noise.
  function checkStatus(site) {
    var noise = noiseCheck(site);
    if (noise && noiseKind(noise) !== "pass") {
      return "fail";
    }
    if (gateApplied(lastScore)) {
      return "pass";
    }
    var flat = flatFraction(site);
    if (flat == null) {
      return "unchecked";
    }
    return flat >= flatThreshold() ? "pass" : "fail";
  }

  function passesAll(site) {
    return checkStatus(site) === "pass";
  }

  // "1 pt short of 50 %" for a fail, "" for a pass.
  function flatShortfall(value) {
    var gap = 100 * (flatThreshold() - value);
    if (!(gap > 0)) {
      return "";
    }
    return (gap < 1 ? "under 1 pt" : Math.round(gap) + " pt") + " short of " +
      Math.round(100 * flatThreshold()) + " %";
  }

  function flatSlopeHint() {
    return scenarioNow().slopeChanged ? " (checked at 8°)" : "";
  }

  function flatCell(site) {
    var value = flatFraction(site);
    if (value == null) {
      return scenarioNow().studiesOk ? verdictHtml("none", "", "not in the 20 m terrain check") : naHtml();
    }
    var pct = Math.round(100 * value) + " %";
    var pass = value >= flatThreshold();
    return verdictHtml(
      pass ? "pass" : "fail",
      pct,
      (pass ? "passes" : "fails, " + flatShortfall(value)) + ": " + pct +
        " of 20 m pixels within 1 km are ≤ 8°" + flatSlopeHint()
    );
  }

  // DEM-noise verdict for the same 240 m cell only.
  function noiseCheck(site) {
    if (!studiesMatch(lastScore)) {
      return null;
    }
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
    var passing = Math.round(found.pass_rate * noiseChecks.draws);
    if (kind === "pass") {
      return "passes in " + passing + "/" + noiseChecks.draws + " DEM-noise draws";
    }
    if (kind === "fail") {
      return "fails in " + noiseChecks.draws + "/" + noiseChecks.draws + " DEM-noise draws";
    }
    return "borderline: passes in " + passing + "/" + noiseChecks.draws + " DEM-noise draws" +
      (found.noise_unstable ? "; sits on the 50 % line" : "");
  }

  function noiseCell(site) {
    var found = noiseCheck(site);
    if (!found && !scenarioNow().studiesOk) {
      return naHtml();
    }
    return verdictHtml(noiseKind(found), "", noiseWords(found));
  }

  function groupText(km) {
    var v = Number(km != null ? km : (meta && meta.defaults && meta.defaults.min_separation_km) || 10);
    return v % 1 ? v.toFixed(1) : String(v);
  }

  // Grouping distance from the input, clamped to what /score accepts.
  function clampGroup(value) {
    var v = Number(value);
    if (!isFinite(v) || String(value).trim() === "") {
      return Number((meta && meta.defaults && meta.defaults.min_separation_km) || 10);
    }
    return Math.min(100, Math.max(0.5, v));
  }

  function renderTableNote() {
    var parts = [];
    parts.push(
      "<p><b>Score</b> 0–1: weighted sum of normalised sunlight, hydrogen and Earth view. " +
      "Sites must pass the slope rule on the 240 m grid. Ranked sites are at least " +
      groupText(lastRequest && lastRequest.min_separation_km) + " km apart.</p>"
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
    parts.push(
      "<p><b>Longest dark</b>: longest run of consecutive hours with no part of the Sun above the " +
      "modelled 240 m horizon, " + escapeHtml(darkPeriod()) +
      " (or inside the sunlight month window), in days. " +
      "Hourly samples (±1 h); ≥ = the run touches the edge of the period. Not scored." +
      (meta && meta.darkness && meta.darkness.note ? " " + escapeHtml(meta.darkness.note) + "." : "") +
      "</p>"
    );
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
        "≈ = the answer flips or sits on the 50 % line. Only " + noiseSiteCount() + " sites were tested.</p>"
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
    cardsClosed = false;
    map.setView([site.lat, site.lon], Math.max(map.getZoom(), 4), { animate: false });
    renderSites(currentSites, currentRadius);
    renderCards();
    // Keep the site visible above a floating card.
    if (document.body.classList.contains("cards-float")) {
      // The floating cards take at most 62 % of the map height (see style.css).
      map.panBy([0, (0.62 * map.getSize().y) / 2], { animate: false });
    }
  }

  // ---- Scenario on the map

  // Keep-out disks around the Artemis III points the request avoided.
  function drawKeepout(spec) {
    if (keepoutLayer) {
      map.removeLayer(keepoutLayer);
      keepoutLayer = null;
    }
    if (!spec || !spec.points || !spec.points.length) {
      return;
    }
    keepoutLayer = L.layerGroup().addTo(map);
    var radiusM = 1000 * Number(spec.radius_km);
    spec.points.forEach(function (point) {
      L.polygon(radiusRing({ lat: point.lat, lon: point.lon }, radiusM), {
        color: "#e60019",
        weight: 1.5,
        dashArray: "5 4",
        fillColor: "url(#keepout-hatch)",
        fillOpacity: 1,
        interactive: false,
      }).addTo(keepoutLayer);
    });
    ensureHatch();
  }

  // Red diagonal hatching for keep-out disks, defined once in Leaflet's SVG.
  function ensureHatch() {
    var svg = map.getPane("overlayPane").querySelector("svg");
    if (!svg || svg.querySelector("#keepout-hatch")) {
      return;
    }
    var ns = "http://www.w3.org/2000/svg";
    var defs = svg.querySelector("defs") || svg.insertBefore(document.createElementNS(ns, "defs"), svg.firstChild);
    var pattern = document.createElementNS(ns, "pattern");
    pattern.setAttribute("id", "keepout-hatch");
    pattern.setAttribute("patternUnits", "userSpaceOnUse");
    pattern.setAttribute("width", "8");
    pattern.setAttribute("height", "8");
    pattern.setAttribute("patternTransform", "rotate(45)");
    var bg = document.createElementNS(ns, "rect");
    bg.setAttribute("width", "8");
    bg.setAttribute("height", "8");
    bg.setAttribute("fill", "rgba(230, 0, 25, 0.10)");
    var line = document.createElementNS(ns, "rect");
    line.setAttribute("width", "2");
    line.setAttribute("height", "8");
    line.setAttribute("fill", "rgba(230, 0, 25, 0.55)");
    pattern.appendChild(bg);
    pattern.appendChild(line);
    defs.appendChild(pattern);
  }

  function renderWindowBadge(months) {
    var badge = document.getElementById("window-badge");
    if (!months) {
      badge.hidden = true;
      return;
    }
    badge.hidden = false;
    badge.textContent = "Sunlight: " + windowLabel(months) + " (modelled daily)";
  }

  // ---- Site cards

  function cardSnapshot(index) {
    var scenario = lastScenario || { isDefault: true, studiesOk: true, parts: [] };
    return {
      site: currentSites[index],
      rank: index + 1,
      leaderTotal: currentSites[0] ? currentSites[0].total : null,
      scenario: scenario,
      scenarioText: scenario.parts.length ? scenario.parts.join(" · ") : "defaults",
      months: (lastRequest && lastRequest.illumination_months) || null,
      maxSlope: lastRequest ? lastRequest.hard.max_slope_deg : null,
    };
  }

  // Why the site sits at its rank: what its score is made of, and the gap to #1.
  function whyRank(card) {
    var c = card.site.contributions || {};
    var parts = [
      ["sunlight", c.illumination],
      ["hydrogen", c.hydrogen],
      ["Earth view", c.earth_visibility],
    ].filter(function (p) {
      return p[1] != null && Number(p[1]) > 0;
    }).sort(function (a, b) {
      return Number(b[1]) - Number(a[1]);
    });
    var made = parts.length
      ? "Score " + Number(card.site.total).toFixed(3) + " = " +
        parts.map(function (p) {
          return p[0] + " " + contribCell(p[1]);
        }).join(" + ")
      : "Score " + Number(card.site.total).toFixed(3);
    var sum = parts.reduce(function (acc, p) {
      return acc + Number(p[1]);
    }, 0);
    var penalty = sum - Number(card.site.total);
    if (parts.length && penalty > 0.0005) {
      made += " − keep-out penalty " + penalty.toFixed(2);
    }
    var gap = "";
    if (card.rank > 1 && card.leaderTotal != null) {
      gap = "; " + (Number(card.leaderTotal) - Number(card.site.total)).toFixed(3) + " below #1";
    } else if (card.rank === 1) {
      gap = "; highest score that passes every hard rule";
    }
    return made + gap + ". Suggested order, not a verdict: check the site before choosing.";
  }

  function fact(label, html) {
    return "<dt>" + label + "</dt><dd>" + html + "</dd>";
  }

  function rawFacts(card, values) {
    if (!values) {
      return fact("Layer values", '<span class="na">loading…</span>');
    }
    var v = values.values || {};
    var out = "";
    out += fact(
      "Sunlight",
      v.illumination == null
        ? '<span class="na">not checked</span>'
        : "lit " + (100 * v.illumination).toFixed(0) + " % of " +
            (card.months ? escapeHtml(windowLabel(card.months)) + " (modelled daily)" : "the time (18.6-yr average)")
    );
    out += darkFacts(card);
    out += fact(
      "Slope (240 m)",
      v.slope == null
        ? '<span class="na">not checked</span>'
        : v.slope.toFixed(1) + "°" + (card.maxSlope != null ? ' <span class="na">limit ' + card.maxSlope + "°</span>" : "")
    );
    // Presentation mode keeps the card short so the check rows stay visible.
    out += fact("Earth view", escapeHtml(earthWords(values, isPresenting())));
    out += fact(
      "Hydrogen",
      v.hydrogen == null ? '<span class="na">not checked</span>' : v.hydrogen.toFixed(0) + " ppm"
    );
    if (v.dem != null && !isPresenting()) {
      out += fact("Elevation", v.dem.toFixed(0) + " m");
    }
    return out;
  }

  function checkFacts(card) {
    var site = card.site;
    var out = "";
    var flat = flatFraction(site);
    if (flat == null) {
      out += fact(
        "Flat ground 1 km",
        card.scenario.studiesOk
          ? verdictHtml("none", "not checked (only the default top 10 were)")
          : verdictHtml("none", "n/a for this scenario")
      );
    } else {
      var pass = flat >= flatThreshold();
      out += fact(
        "Flat ground 1 km",
        verdictHtml(pass ? "pass" : "fail", "") + " " + Math.round(100 * flat) +
          " % of 20 m pixels ≤ 8° — " + (pass ? "passes the 50 % rule" : "fails, " + flatShortfall(flat)) +
          (card.scenario.slopeChanged ? ' <span class="na">(checked at 8°)</span>' : "")
      );
    }
    var noise = noiseCheck(site);
    if (!noise) {
      out += fact(
        "DEM-noise",
        verdictHtml("none", card.scenario.studiesOk ? "not checked (" + noiseSiteCount() + " sites were)" : "n/a for this scenario")
      );
    } else {
      out += fact("DEM-noise", verdictHtml(noiseKind(noise), "") + " " + escapeHtml(noiseWords(noise)));
    }
    if (!stability || !stability.available) {
      out += fact("Weight-robust", verdictHtml("none", "not checked"));
    } else {
      var row = stabilityRow(site);
      var freq = row ? row.frequency : 0;
      var win = winnerFrequency(site);
      var text =
        "top 10 in " + (100 * freq).toFixed(freq === 1 || freq === 0 ? 0 : 1) + " % of " +
        stability.draw_count + " weight draws" +
        (win != null ? "; #1 in " + (100 * win).toFixed(1) + " %" : "");
      out += fact(
        "Weight-robust",
        card.scenario.studiesOk
          ? escapeHtml(text)
          : row
            ? '<span class="na">' + escapeHtml(text) + " (default scenario only)</span>"
            : '<span class="na">n/a for this scenario</span>'
      );
    }
    var near = site.nearest
      ? escapeHtml(site.nearest) + ", " + Number(site.distance_km).toFixed(1) +
        ' km <span class="na">(approximate point, not a NASA centre)</span>'
      : '<span class="na">none cited</span>';
    out += fact("Nearest Artemis III", near);
    return out;
  }

  function cardHtml(card, values, kind) {
    var site = card.site;
    var c = site.contributions || {};
    var parts = [
      ["Sunlight", c.illumination, "c-sun"],
      ["Hydrogen", c.hydrogen, "c-hyd"],
      ["Earth view", c.earth_visibility, "c-earth"],
    ];
    var bar = parts
      .map(function (p) {
        var w = Math.max(0, Math.min(1, Number(p[1]) || 0));
        return '<i class="' + p[2] + '" style="width:' + (100 * w).toFixed(1) + '%" title="' + p[0] + " " + contribCell(p[1]) + '"></i>';
      })
      .join("");
    var key = parts
      .map(function (p) {
        return '<span><i class="sw ' + p[2] + '"></i>' + p[0] + " " + contribCell(p[1]) + "</span>";
      })
      .join("");
    var buttons =
      kind === "pinned"
        ? '<button type="button" class="card-btn" data-act="unpin">Unpin</button>'
        : '<button type="button" class="card-btn" data-act="pin" title="Keep this card to compare with another site">Compare</button>';
    buttons += '<button type="button" class="card-btn card-close" data-act="close-' + kind + '" aria-label="Close card">×</button>';
    return (
      '<article class="card' + (kind === "pinned" ? " pinned" : "") + '">' +
      '<header class="card-head"><span class="card-rank">#' + pad2(card.rank) + "</span>" +
      '<span class="card-loc">' + fmtLocation(site.lat, site.lon) + "</span>" +
      '<span class="card-actions">' + buttons + "</span></header>" +
      (kind === "pinned" ? '<p class="card-scn">Pinned · scored under: ' + escapeHtml(card.scenarioText) + "</p>" : "") +
      '<div class="card-score"><span class="card-total">' + Number(site.total).toFixed(3) + "</span>" +
      '<span class="card-bar">' + bar + "</span></div>" +
      '<p class="card-key">' + key + "</p>" +
      '<p class="card-why">' + escapeHtml(whyRank(card)) + "</p>" +
      '<dl class="card-facts">' + rawFacts(card, values) + checkFacts(card) + "</dl>" +
      "</article>"
    );
  }

  async function renderCards() {
    // Only the newest call may redraw after its /site fetches return.
    var seq = ++cardsSeq;
    var list = [];
    if (pinned) {
      list.push({ card: pinned, kind: "pinned" });
    }
    if (!cardsClosed && selectedRank != null && currentSites && currentSites[selectedRank]) {
      var current = cardSnapshot(selectedRank);
      var same =
        pinned && pinned.site.row === current.site.row && pinned.site.col === current.site.col &&
        pinned.scenarioText === current.scenarioText;
      if (!same) {
        list.push({ card: current, kind: "current" });
      }
    }
    document.body.classList.toggle("has-cards", list.length > 0);
    els.cards.classList.toggle("two", list.length > 1);
    if (!list.length) {
      els.cards.innerHTML = "";
      els.cards.appendChild(els.cardsEmpty);
      return;
    }
    var draw = function () {
      els.cards.innerHTML = list
        .map(function (item) {
          var values = siteValueCache[siteValueKey(item.card.site, item.card.months)] || null;
          return cardHtml(item.card, values, item.kind);
        })
        .join("");
    };
    draw();
    var missing = list.filter(function (item) {
      return !siteValueCache[siteValueKey(item.card.site, item.card.months)];
    });
    if (missing.length) {
      await Promise.all(
        missing.map(function (item) {
          return siteValues(item.card.site, item.card.months).catch(function () {
            return null;
          });
        })
      );
      if (seq !== cardsSeq) {
        return;
      }
      draw();
    }
  }

  function onCardClick(event) {
    var button = event.target.closest("button[data-act]");
    if (!button) {
      return;
    }
    var act = button.getAttribute("data-act");
    if (act === "pin" && selectedRank != null) {
      pinned = cardSnapshot(selectedRank);
    } else if (act === "unpin" || act === "close-pinned") {
      pinned = null;
    } else if (act === "close-current") {
      cardsClosed = true;
    }
    renderCards();
  }

  // Presentation mode and short screens have no room under the table, so
  // the cards float over the bottom of the map there.
  function placeCards() {
    var float = isPresenting() || window.innerHeight < 900;
    document.body.classList.toggle("cards-float", float);
    var target = float ? els.mapFrame : els.sitesPanel;
    if (els.cards.parentNode !== target) {
      target.appendChild(els.cards);
    }
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
          className:
            "site-mark" + (selectedRank === index ? " active" : "") +
            (els.onlyPassing.checked && !passesAll(site) ? " dim" : ""),
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
      tr.className = (selectedRank === index ? "active " : "") + (passesAll(site) ? "passes-all" : "fails-checks");
      tr.innerHTML =
        '<td class="num rank">' +
        pad2(index + 1) +
        previousRankHtml(site, index) +
        '</td><td class="loc">' +
        fmtLocation(site.lat, site.lon) +
        '</td><td class="num score-cell">' +
        Number(site.total).toFixed(3) +
        scoreBar(contrib) +
        '</td><td class="num contrib">' +
        contribCell(contrib.illumination) +
        '</td><td class="num dark-col">' +
        darkCell(site, lastRequest && lastRequest.illumination_months) +
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
    // Shown only when the filter hides every row.
    var empty = document.createElement("tr");
    empty.className = "filter-empty";
    empty.innerHTML =
      '<td colspan="' + columnCount() + '">No site in this list passes every check run on it.</td>';
    els.sites.appendChild(empty);
    syncFilter();
    drawMarkers();
  }

  function syncFilter() {
    var on = els.onlyPassing.checked;
    els.sitesTable.classList.toggle("only-passing-on", on);
    var empty = els.sites.querySelector(".filter-empty");
    if (empty) {
      empty.hidden = !on || els.sites.querySelectorAll("tr.passes-all").length > 0;
    }
    if (map) {
      drawMarkers();
    }
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

  // ---- 20 m flat-ground rule toggle

  // Enabled only when the gate layer is on this machine and the max slope
  // is the one it was built for.
  function updateTerrainToggle() {
    var gate = (meta && meta.terrain_gate) || null;
    if (!gate) {
      els.terrain.disabled = true;
      els.terrain.checked = false;
      els.terrainNote.textContent = "This server has no 20 m flat-ground rule.";
      return;
    }
    var built = gate.layer_max_slope_deg != null ? gate.layer_max_slope_deg : (meta.defaults || {}).max_slope_deg;
    if (!gate.available) {
      els.terrain.disabled = true;
      els.terrain.checked = false;
      els.terrainNote.textContent =
        "Unavailable on this machine: " + (gate.reason || "layer missing") + ".";
    } else if (Number(els.slope.value) !== Number(built)) {
      els.terrain.disabled = true;
      els.terrain.checked = false;
      els.terrainNote.textContent =
        "Built for max slope " + built + "° only; set max slope to " + built + "° to use it.";
    } else {
      if (els.terrain.disabled) {
        // Back from disabled: restore the user's last choice, else the default.
        els.terrain.checked = terrainChoice != null ? terrainChoice : !!gate.enabled_by_default;
      }
      els.terrain.disabled = false;
      els.terrainNote.textContent =
        "Drops cells with under " + Math.round(100 * gate.min_fraction) +
        " % of 20 m pixels ≤ " + built + "° within " + gate.radius_m / 1000 + " km.";
    }
  }

  // ---- Scenario vs study defaults

  function normalisedWeights(weights) {
    var total = 0;
    Object.keys(weights).forEach(function (key) {
      total += Number(weights[key]) || 0;
    });
    var out = {};
    Object.keys(weights).forEach(function (key) {
      out[key] = total > 0 ? (Number(weights[key]) || 0) / total : 0;
    });
    return out;
  }

  function weightText(weights) {
    var w = normalisedWeights(weights);
    return (
      "sunlight " + Math.round(100 * (w.illumination || 0)) + " / hydrogen " +
      Math.round(100 * (w.hydrogen || 0)) + " / Earth view " +
      Math.round(100 * (w.earth_visibility || 0)) + " %"
    );
  }

  // How a request differs from the defaults the studies were run with.
  function scenarioOf(request) {
    var defaults = (meta && meta.defaults) || {};
    var parts = [];
    var a = normalisedWeights(request.weights || {});
    var b = normalisedWeights(defaults.weights || {});
    var weightsChanged = Object.keys(Object.assign({}, a, b)).some(function (key) {
      return Math.abs((a[key] || 0) - (b[key] || 0)) > 1e-6;
    });
    if (weightsChanged) {
      parts.push("weights " + weightText(request.weights));
    }
    var slopeChanged = Number(request.hard.max_slope_deg) !== Number(defaults.max_slope_deg);
    if (slopeChanged) {
      parts.push("max slope " + request.hard.max_slope_deg + "°");
    }
    var radiusChanged = Number(request.hard.slope_radius_m) !== Number(defaults.slope_radius_m || 0);
    if (radiusChanged) {
      parts.push("slope checked within " + request.hard.slope_radius_m + " m");
    }
    var groupDefault = Number(defaults.min_separation_km || 10);
    if (request.min_separation_km != null && Number(request.min_separation_km) !== groupDefault) {
      parts.push("sites grouped within " + request.min_separation_km + " km");
    }
    if (request.keepout) {
      parts.push(
        "keep-out " + request.keepout.radius_km + " km around " + request.keepout.points.length +
        " Artemis III points (" + (request.keepout.mode === "hard" ? "excluded" : "penalty " + request.keepout.penalty) + ")"
      );
    }
    if (request.illumination_months) {
      parts.push("sunlight " + windowLabel(request.illumination_months) + " (modelled daily)");
    }
    // The gate switch is shown as a scenario change but does not by itself
    // move away from the served studies;
    // /score's ranking_basis.studies_match decides that.
    var isDefault = parts.length === 0;
    var gateSpec = (meta && meta.terrain_gate) || {};
    if (request.hard.terrain_1km != null && request.hard.terrain_1km !== !!gateSpec.enabled_by_default) {
      parts.push("20 m flat-ground rule " + (request.hard.terrain_1km ? "on" : "off"));
    }
    return {
      isDefault: isDefault,
      // The 20 m checks were run at 8°; other slope limits make them moot.
      slopeChanged: slopeChanged,
      parts: parts,
    };
  }

  function siteValueKey(site, months) {
    return site.row + ":" + site.col + "|" + (months ? months.start + ".." + months.end : "");
  }

  async function siteValues(site, months) {
    var key = siteValueKey(site, months);
    if (siteValueCache[key]) {
      return siteValueCache[key];
    }
    var path = "/site/" + site.row + "/" + site.col;
    if (months) {
      path += "?start=" + encodeURIComponent(months.start) + "&end=" + encodeURIComponent(months.end);
    }
    var response = await fetch(path);
    if (!response.ok) {
      return null;
    }
    var body = await response.json();
    siteValueCache[key] = body;
    return body;
  }

  // Headline place: the Artemis III candidate region when the site is within
  // 10 km of its cited (approximate) point; no false-precision distance.
  function regionName(site) {
    if (site.nearest && site.distance_km != null && site.distance_km <= 10) {
      return "near NASA's " + escapeHtml(site.nearest) + " candidate region";
    }
    return "at " + fmtLocation(site.lat, site.lon);
  }

  // Nearest cited Artemis III point within 10 km, else coordinates.
  function placeName(site) {
    if (site.nearest && site.distance_km != null && site.distance_km <= 10) {
      return (
        "near " + escapeHtml(site.nearest) + " (" + Number(site.distance_km).toFixed(1) + " km)"
      );
    }
    return "at " + fmtLocation(site.lat, site.lon);
  }

  function sunlightWords(values, months) {
    var v = values && values.values ? values.values.illumination : null;
    if (v == null) {
      return "sunlight not checked";
    }
    return (
      "lit " + Math.round(100 * v) + " % of " +
      (months ? windowLabel(months) + " (modelled daily)" : "the time (18.6-yr average)")
    );
  }

  function earthWords(values, short) {
    var v = values && values.values ? values.values.earth_visibility : null;
    if (v == null) {
      return "Earth view not checked";
    }
    // Elevation of the sub-Earth point above the local horizon at mean
    // libration. Libration swings Earth by about ±7°, so near the pole
    // direct contact comes and goes; contact windows are not modelled.
    var where = v < 0
      ? "Earth " + Math.abs(v).toFixed(1) + "° below the horizon at mean libration"
      : "Earth " + v.toFixed(1) + "° above the horizon at mean libration";
    return where + (short
      ? " (contact intermittent; not modelled)"
      : " (libration moves Earth by about ±7°, so contact near the pole is intermittent; contact windows not modelled)");
  }

  function flatWords(site, scenario) {
    var value = flatFraction(site);
    if (value == null) {
      return scenario.studiesOk ? "flat ground within 1 km not checked" : "flat ground within 1 km n/a for this scenario";
    }
    var pass = value >= flatThreshold();
    return (
      Math.round(100 * value) + " % flat ground within 1 km (20 m" + flatSlopeHint() + "; " +
      (pass ? "passes the 50 % rule" : "fails — " + flatShortfall(value)) + ")"
    );
  }

  function robustWords(site, scenario) {
    if (!stability || !stability.available) {
      return "weight robustness not checked";
    }
    if (!scenario.studiesOk) {
      return "weight robustness n/a for this scenario";
    }
    var row = stabilityRow(site);
    var freq = row ? row.frequency : 0;
    var win = winnerFrequency(site);
    return (
      "in the top 10 in " + (100 * freq).toFixed(freq === 1 || freq === 0 ? 0 : 1) +
      " % of " + stability.draw_count + " weight draws" +
      (win != null ? " and #1 in " + (100 * win).toFixed(1) + " %" : "")
    );
  }

  // The optional 20 m terrain gate from /score, read but never required.
  // The 20 m terrain gate from /score in words, or null if the server has none.
  function terrainGateText(payload) {
    var gate = payload.terrain_gate;
    if (gate == null) {
      return null;
    }
    var detail = payload.terrain_gate_detail || {};
    var status = typeof gate === "string" ? gate : gate.status;
    if (status === "applied") {
      var removed =
        detail.cells_removed != null
          ? "; " + Number(detail.cells_removed).toLocaleString("en") +
            (Number(detail.cells_removed) === 1 ? " cell" : " cells") + " removed"
          : "";
      return (
        "20 m flat-ground rule: on (≥ " + Math.round(100 * (detail.min_fraction == null ? 0.5 : detail.min_fraction)) +
        " % flat within " + ((detail.radius_m || 1000) / 1000) + " km" + removed + ")"
      );
    }
    var words = {
      disabled: "off",
      unavailable: "unavailable",
      slope_mismatch: "not applied (built for " + (detail.layer_max_slope_deg != null ? detail.layer_max_slope_deg : 8) + "° only)",
    };
    var text = "20 m flat-ground rule: " + (words[status] || String(status));
    if (status === "unavailable" && detail.message) {
      text += " — " + detail.message.replace(/^20 m terrain rule not applied: /, "");
    }
    return text;
  }

  // Second banner line: the best ranked site that passes every check run on it.
  function passLine(sites, payload) {
    var prefix = gateApplied(payload) ? "All ranked sites pass the 20 m flat-ground rule. " : "";
    var index = -1;
    sites.some(function (site, i) {
      if (passesAll(site)) {
        index = i;
        return true;
      }
      return false;
    });
    if (index === 0) {
      return prefix + "<b>#1 also passes every check run on it</b> (20 m flat ground" +
        (noiseCheck(sites[0]) ? ", DEM-noise" : "; DEM-noise not run") + ").";
    }
    if (index > 0) {
      var site = sites[index];
      var flat = flatFraction(site);
      var parts = [];
      if (flat != null) {
        parts.push(Math.round(100 * flat) + " % flat ground within 1 km (20 m)");
      }
      var noise = noiseCheck(site);
      parts.push(noise ? noiseWords(noise) : "DEM-noise not run");
      return (
        prefix + "<b>Highest-ranked site that passes every check run on it: #" + (index + 1) + " " + placeName(site) +
        "</b> — " + escapeHtml(parts.join(", ")) + "."
      );
    }
    var checked = sites.filter(function (site) {
      return checkStatus(site) !== "unchecked";
    }).length;
    if (!checked) {
      return prefix + "No ranked site here has had the 20 m flat-ground check, so none can be called passing.";
    }
    return (
      prefix + "<b>No ranked site passes every check run on it</b>: " + checked + " of " + sites.length +
      " were checked and each fails flat ground or DEM-noise; the rest are not checked."
    );
  }

  function presentWords(site, values, months) {
    var out = [];
    var v = values && values.values ? values.values.illumination : null;
    if (v != null) {
      out.push("lit " + Math.round(100 * v) + " % of the time");
    }
    var d = darkOf(site, months);
    if (d && d.never) {
      out.push("never lit");
    } else if (d) {
      out.push("longest night " + (d.censored ? "≥ " : "") + daysText(d.hours) + " days");
    }
    var flat = flatFraction(site);
    if (flat != null) {
      out.push(Math.round(100 * flat) + " % flat ground within 1 km");
    }
    return out.length ? out : ["layer values not available"];
  }

  async function renderAnswer(payload, request, seq) {
    var scenario = lastScenario;
    var meta2 = [];
    var gateText = terrainGateText(payload);
    if (gateText) {
      meta2.push('<span class="tag">' + escapeHtml(gateText) + "</span>");
    }
    if (!studiesMatch(payload)) {
      meta2.push(
        '<span class="tag changed" title="' + escapeHtml(studiesNote()) + '">Weight-robust and DEM-noise n/a: ' +
          escapeHtml(studiesWhy(payload)) + "</span>"
      );
    }
    meta2.push(
      !scenario.parts.length
        ? '<span class="tag">Scenario: defaults (' + escapeHtml(weightText(request.weights)) +
            ", slope ≤ " + request.hard.max_slope_deg + "°, 18.6-yr average sunlight)</span>"
        : '<span class="tag changed">Scenario: ' + escapeHtml(scenario.parts.join(" · ")) + "</span>"
    );
    els.answerMeta.innerHTML = meta2.join("");
    var best = (payload.sites || [])[0];
    els.answerPass.innerHTML = best ? passLine(payload.sites, payload) : "";
    if (!best) {
      els.answerMain.textContent = "No site passes the slope rule in this scenario.";
      return;
    }
    var months = request.illumination_months || null;
    var head = "<b>Suggested #1 " + regionName(best) + "</b>";
    var steps = document.getElementById("answer-steps");
    steps.textContent =
      "Requirements → one map layer per rule → score what is left → group points within " +
      groupText(request.min_separation_km) + " km into sites → suggested order with reasons → a person checks";
    els.answerMain.innerHTML = head + " — loading layer values…";
    var values = null;
    try {
      values = await siteValues(best, months);
    } catch (err) {
      values = null;
    }
    if (seq !== scoreSeq) {
      return;
    }
    if (isPresenting()) {
      // Three facts a room can read: sunlight, longest night, flat ground.
      els.answerMain.innerHTML = head + " — " + presentWords(best, values, months).join(" · ");
      return;
    }
    els.answerMain.innerHTML =
      head + " — " + [
        sunlightWords(values, months),
        darkWords(best, months),
        earthWords(values, true),
        flatWords(best, scenario),
        robustWords(best, scenario),
      ].filter(Boolean).join(", ") + ".";
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

  // ---- Sunlight month window

  function monthText(value) {
    var parts = value.split("-");
    return MONTHS[Number(parts[1]) - 1] + " " + parts[0];
  }

  // "Mar 2027", "Jan–Mar 2027", or "Nov 2027 – Feb 2028".
  function windowLabel(months) {
    if (months.start === months.end) {
      return monthText(months.start);
    }
    if (months.start.slice(0, 4) === months.end.slice(0, 4)) {
      return monthText(months.start).slice(0, 3) + "–" + monthText(months.end);
    }
    return monthText(months.start) + " – " + monthText(months.end);
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
      note +=
        "Using modelled daily sunlight for " + windowLabel(months) +
        " (horizon model validated against the NASA average map, RMSE 0.02).";
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
      // Sent only when the rule can apply; otherwise the server uses its
      // default and reports why the rule is off.
      n: (meta.defaults && meta.defaults.n) || 10,
    };
    request.min_separation_km = clampGroup(els.groupKm.value);
    if (!els.terrain.disabled) {
      request.hard.terrain_1km = els.terrain.checked;
    }
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
    if (showKeepoutError() || showWeightsError()) {
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
    if (info.defaults && info.defaults.min_separation_km != null) {
      els.groupKm.value = info.defaults.min_separation_km;
    }
    syncSliderLabels();
  }

  // Each slider shows its share of the total, the weight the score uses.
  function syncSliderLabels() {
    var shares = normalisedWeights(currentWeights());
    els.illumVal.textContent = Math.round(100 * shares.illumination) + " %";
    els.hydroVal.textContent = Math.round(100 * shares.hydrogen) + " %";
    els.earthVal.textContent = Math.round(100 * shares.earth_visibility) + " %";
    showWeightsError();
  }

  function weightsError() {
    var w = currentWeights();
    return w.illumination + w.hydrogen + w.earth_visibility > 0
      ? ""
      : "All weights are zero. Set at least one above 0.";
  }

  function showWeightsError() {
    var message = weightsError();
    var box = document.getElementById("weights-error");
    box.textContent = message;
    box.hidden = !message;
    return message;
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
      zoomSnap: 1,
      minZoom: 1,
      maxZoom: 5,
      zoomControl: false,
      attributionControl: false,
      boxZoom: false,
    });
    // Zoom 1 shows the whole 80°S cap; zoom 2 opens on the pole out to about 85°S.
    map.setView([-90, 0], Number(PARAMS.get("zoom")) || 2, { animate: false });
    map.createPane("basePane").style.zIndex = 250;
    map.createPane("scorePane").style.zIndex = 350;
    map.createPane("labelPane").style.zIndex = 450;
    map.getPane("labelPane").style.pointerEvents = "none";
    addTerrain(info);
    addGraticule(crs);
    addPlaceLabels();
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
    if (showKeepoutError() || showWeightsError()) {
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
        if (seq !== scoreSeq) {
          return;
        }
      } else {
        setOverlay("data:image/png;base64," + payload.png, payload.bounds, "score");
        els.layer.value = "score";
      }
      selectedRank = null;
      // Ranks before a scenario change, shown in grey next to the new ranks.
      var requestKey = JSON.stringify(request);
      if (lastRequest && requestKey !== JSON.stringify(lastRequest) && currentSites) {
        previousRanks = {};
        currentSites.forEach(function (site, i) {
          previousRanks[siteKey(site)] = i + 1;
        });
      } else if (!lastRequest) {
        previousRanks = null;
      }
      lastRequest = request;
      lastKeepoutLists = els.keepoutLists.value;
      lastScenario = scenarioOf(request);
      lastScenario.studiesOk = lastScenario.isDefault && studiesMatch(payload);
      var legendNa = document.getElementById("legend-na");
      legendNa.hidden = lastScenario.studiesOk;
      legendNa.lastChild.textContent = studiesMatch(payload)
        ? " not in that study (studies used the default scenario)"
        : " not comparable (" + studiesWhy(payload) + ")";
      renderSites(payload.sites, request.hard.slope_radius_m);
      renderCards();
      renderArtemis(payload.artemis);
      drawKeepout(request.keepout);
      renderWindowBadge(request.illumination_months);
      renderAnswer(payload, request, seq);
      els.csv.disabled = false;
      els.geojson.disabled = false;
      els.link.disabled = false;
      writeUrl();
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
      var months = name === "illumination" || name === "longest_dark" ? currentWindow() : null;
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
      pointToLayer: function (feature, latlng) {
        return L.marker(latlng, {
          icon: L.divIcon({
            className: "artemis-mark",
            html: '<span class="artemis-square"></span>',
            iconSize: [10, 10],
            iconAnchor: [5, 5],
          }),
          keyboard: false,
        });
      },
      onEachFeature: function (feature, layer) {
        var props = feature.properties || {};
        layer.bindTooltip(
          escapeHtml(props.name || "Artemis III region") +
            ' <span class="tip-note">approx. point, ' + escapeHtml((props.lists || []).join(" + ")) + " list</span>",
          { direction: "top", offset: [0, -6] }
        );
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

  // In presentation mode the slope limit stays at the default, the only limit
  // the 20 m flat-ground and robustness studies exist for.
  function lockSlope(on) {
    var note = document.getElementById("slope-lock-note");
    var base = meta && meta.defaults ? meta.defaults.max_slope_deg : null;
    els.slope.disabled = on;
    note.hidden = !on;
    if (on && base != null && Number(els.slope.value) !== Number(base)) {
      els.slope.value = base;
      updateTerrainToggle();
      if (lastRequest) {
        scheduleScore();
      }
    }
  }

  function setPresenting(on) {
    // The page sizes in rem, so the root element carries the class too.
    document.documentElement.classList.toggle("present", on);
    lockSlope(on);
    document.body.classList.toggle("present", on);
    els.present.setAttribute("aria-pressed", on ? "true" : "false");
    try {
      window.localStorage.setItem(PRESENT_KEY, on ? "1" : "0");
    } catch (err) {
      // Storage can be blocked; the toggle still works for this page.
    }
    placeCards();
    // Cards merge the Stage G rows in presentation mode.
    if (currentSites) {
      renderCards();
    }
    if (lastScore && lastRequest) {
      renderAnswer(lastScore, lastRequest, scoreSeq);
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
      writeUrl();
    });
    els.cards.addEventListener("click", onCardClick);
    els.onlyPassing.addEventListener("change", function () {
      syncFilter();
      writeUrl();
    });
    document.getElementById("present-csv").addEventListener("click", function () {
      els.csv.click();
    });
    window.addEventListener("resize", debounce(placeCards, 150));
    els.railToggle.addEventListener("click", function () {
      setRail(!railOpen());
      writeUrl();
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
      writeUrl();
    });
    els.link.addEventListener("click", function () {
      copyScenarioLink();
    });
    els.artemis.addEventListener("change", function () {
      toggleArtemis();
      writeUrl();
    });
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
    els.terrain.addEventListener("change", function () {
      terrainChoice = els.terrain.checked;
      scheduleScore();
    });
    els.groupKm.addEventListener("change", function () {
      els.groupKm.value = clampGroup(els.groupKm.value);
      scheduleScore();
    });
    ["input", "change"].forEach(function (type) {
      els.slope.addEventListener(type, updateTerrainToggle);
    });

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
    els.terrain.disabled = true;
    updateTerrainToggle();
    initMap(meta);
    await loadAnnotations();
    await loadArtemisPoints();
    if (PARAMS.get("artemis") === "1") {
      els.artemis.checked = true;
      toggleArtemis();
    }

    if (!meta.ingested) {
      showNotIngested(meta.ingest_command);
    } else {
      await runScore();
      // A scenario in the URL is scored after the defaults, so the table
      // shows the previous ranks: ?w=0.6,0.2,0.2&slope=6&keepout=5&start=2027-01&end=2027-03
      if (applyUrlScenario()) {
        await runScore({ keepLayer: true });
      }
      applyUrlSite();
      applyUrlLayer();
    }
  }

  // Keep the address bar in step with the last scored scenario, so the page
  // URL (and "Copy scenario link") reopens the same state via applyUrlScenario.
  function writeUrl() {
    if (!lastRequest || !window.history || !window.history.replaceState) {
      return;
    }
    var params = new URLSearchParams();
    if (isPresenting()) {
      params.set("present", "1");
    }
    if (PARAMS.get("zoom") != null) {
      params.set("zoom", PARAMS.get("zoom"));
    }
    if (PARAMS.has("rail") || railOpen() === isPresenting()) {
      // Only when the drawer differs from the mode's default (or was set).
      params.set("rail", railOpen() ? "1" : "0");
    }
    // The weights that were scored, not unscored slider moves.
    var w = lastRequest.weights || {};
    params.set(
      "w",
      [w.illumination || 0, w.hydrogen || 0, w.earth_visibility || 0].map(String).join(",")
    );
    params.set("slope", String(lastRequest.hard.max_slope_deg));
    params.set("sr", String(lastRequest.hard.slope_radius_m));
    if (lastRequest.hard.terrain_1km != null) {
      params.set("terrain", lastRequest.hard.terrain_1km ? "1" : "0");
    }
    if (lastRequest.min_separation_km != null) {
      params.set("group", String(lastRequest.min_separation_km));
    }
    if (lastRequest.keepout) {
      params.set("keepout", String(lastRequest.keepout.radius_km));
      params.set("kmode", lastRequest.keepout.mode);
      params.set("klists", lastKeepoutLists || els.keepoutLists.value);
      if (lastRequest.keepout.mode === "penalty") {
        params.set("kpen", String(lastRequest.keepout.penalty));
      }
    }
    if (lastRequest.illumination_months) {
      params.set("start", lastRequest.illumination_months.start);
      params.set("end", lastRequest.illumination_months.end);
    }
    if (els.layer.value && els.layer.value !== "score") {
      params.set("layer", els.layer.value);
    }
    if (els.onlyPassing.checked) {
      params.set("only", "1");
    }
    if (els.artemis.checked) {
      params.set("artemis", "1");
    }
    window.history.replaceState(null, "", "?" + params.toString());
  }

  async function copyScenarioLink() {
    writeUrl();
    var label = els.link.textContent;
    try {
      await navigator.clipboard.writeText(window.location.href);
      els.link.textContent = "Link copied";
    } catch (err) {
      // Clipboard blocked: the address bar already holds the link.
      els.link.textContent = "Copy it from the address bar";
    }
    setTimeout(function () {
      els.link.textContent = label;
    }, 2000);
  }

  // ?layer=longest_dark shows one map layer after scoring (demos, screenshots).
  function applyUrlLayer() {
    var name = PARAMS.get("layer");
    if (!name) {
      return;
    }
    var option = Array.prototype.filter.call(els.layer.options, function (opt) {
      return opt.value === name && !opt.disabled;
    })[0];
    if (option) {
      els.layer.value = name;
      showSingleLayer(name);
      writeUrl();
    }
  }

  // ?pin=N&site=M, applied after the last score so a URL scenario keeps them.
  function applyUrlSite() {
    if (PARAMS.get("only") === "1") {
      els.onlyPassing.checked = true;
      syncFilter();
    }
    if (!PARAMS.get("site") || !currentSites || !currentSites.length) {
      return;
    }
    var pinIndex = Number(PARAMS.get("pin")) - 1;
    if (pinIndex >= 0 && pinIndex < currentSites.length) {
      selectedRank = pinIndex;
      pinned = cardSnapshot(pinIndex);
    }
    var wanted = Number(PARAMS.get("site")) - 1;
    if (wanted >= 0 && wanted < currentSites.length) {
      selectSite(wanted);
    }
  }

  function applyUrlScenario() {
    var changed = false;
    var w = (PARAMS.get("w") || "").split(",").map(Number);
    if (w.length === 3 && w.every(function (x) { return x >= 0 && x <= 1; })) {
      els.illum.value = w[0];
      els.hydro.value = w[1];
      els.earth.value = w[2];
      syncSliderLabels();
      changed = true;
    }
    // Presentation mode keeps the default slope limit (see lockSlope).
    if (PARAMS.get("slope") && !isPresenting()) {
      els.slope.value = Number(PARAMS.get("slope"));
      updateTerrainToggle();
      changed = true;
    }
    if (PARAMS.get("terrain") && !els.terrain.disabled) {
      els.terrain.checked = PARAMS.get("terrain") === "1";
      terrainChoice = els.terrain.checked;
      changed = true;
    }
    if (PARAMS.get("group")) {
      els.groupKm.value = clampGroup(PARAMS.get("group"));
      changed = true;
    }
    if (PARAMS.get("sr") && Number(PARAMS.get("sr")) >= 0) {
      els.slopeRadius.value = Number(PARAMS.get("sr"));
      changed = true;
    }
    if (PARAMS.get("keepout")) {
      els.keepout.checked = true;
      els.keepoutRadius.value = Number(PARAMS.get("keepout"));
      if (PARAMS.get("kmode") === "hard" || PARAMS.get("kmode") === "penalty") {
        els.keepoutMode.value = PARAMS.get("kmode");
      }
      if (PARAMS.get("klists") != null) {
        var lists = PARAMS.get("klists");
        var known = Array.prototype.some.call(els.keepoutLists.options || [], function (opt) {
          return opt.value === lists;
        });
        if (known) {
          els.keepoutLists.value = lists;
        }
      }
      if (PARAMS.get("kpen") != null && isFinite(Number(PARAMS.get("kpen")))) {
        els.keepoutPenalty.value = Number(PARAMS.get("kpen"));
      }
      updateKeepoutNote();
      changed = true;
    }
    if (PARAMS.get("start") && PARAMS.get("end")) {
      els.windowStart.value = PARAMS.get("start");
      els.windowEnd.value = PARAMS.get("end");
      updateWindowNote();
      changed = true;
    }
    return changed;
  }

  main();
})();
