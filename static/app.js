(function () {
  "use strict";

  var PROJ4 = "+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs";
  var VIRIDIS =
    "linear-gradient(to right, #440154, #482878, #3e4989, #31688e, #26828e, " +
    "#1f9e89, #35b779, #6ece58, #b5de2b, #fde725)";
  var GREY = "linear-gradient(to right, #000000, #ffffff)";
  var PRESENT_KEY = "anuma.presentation";

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
    present: document.getElementById("present-toggle"),
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

  function pad2(n) {
    return String(n).padStart(2, "0");
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
      els.legendNote.textContent = "white = fails slope rule";
      return;
    }
    var info = ((meta && meta.layers) || []).filter(function (layer) {
      return layer.name === name;
    })[0] || {};
    var unit = info.unit ? " (" + info.unit + ")" : "";
    els.legendTitle.textContent = layerLabel(name) + unit;
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

  function stabilityCell(site) {
    var row = stabilityById[siteKey(site)];
    if (!row) {
      return "–";
    }
    var text = (100 * row.frequency).toFixed(1) + " %";
    var win = winnerFrequency(site);
    if (win != null) {
      return '<span title="Winner in ' + (100 * win).toFixed(1) + ' % of draws">' + text + "</span>";
    }
    return text;
  }

  // Nearest Stage B check within its match distance, compared in grid metres.
  function stageBCheck(site) {
    if (!checks || !checks.sites) {
      return null;
    }
    var p = proj4(PROJ4, [site.lon, site.lat]);
    var best = null;
    var bestM = Infinity;
    checks.sites.forEach(function (check) {
      var q = proj4(PROJ4, [check.lon, check.lat]);
      var d = Math.hypot(p[0] - q[0], p[1] - q[1]);
      if (d < bestM) {
        bestM = d;
        best = check;
      }
    });
    return bestM <= checks.match_km * 1000 ? best : null;
  }

  function flagHtml(site) {
    var check = stageBCheck(site);
    if (!check) {
      return "";
    }
    var flag = checks.flag;
    var value = check.fraction_le_8deg[flag.field];
    if (!(value < flag.below)) {
      return "";
    }
    var title =
      flag.text + ": " + Math.round(100 * value) + " % of 20 m pixels ≤ 8° within 1 km (job " +
      checks.source.job + ")";
    return ' <span class="flag" title="' + title + '" aria-label="' + title + '">⚠</span>';
  }

  function renderTableNote() {
    var parts = [];
    if (stability && stability.available) {
      var text =
        "Top-10 %: share of " + stability.draw_count + " weight draws (job " +
        stability.job_id + ") with the site in the top 10; " + stability.note + ".";
      var winner = (stability.winners || [])[0];
      var index = -1;
      if (winner && currentSites) {
        currentSites.forEach(function (site, i) {
          if (site.row === winner.row && site.col === winner.col) {
            index = i;
          }
        });
      }
      if (index >= 0) {
        text += " Site " + pad2(index + 1) + " wins in " + (100 * winner.frequency).toFixed(1) + " % of draws.";
      }
      parts.push(text + " – = not in the study.");
    } else {
      parts.push("Top-10 %: Monte Carlo study not on disk.");
    }
    if (checks) {
      parts.push(
        "⚠ = " + checks.flag.text + ", job " + checks.source.job +
        "; only the default top 10 were checked."
      );
    }
    els.tableNote.textContent = parts.join(" ");
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
        site.distance_km == null ? "" : " (" + Number(site.distance_km).toFixed(1) + " km)";
      return site.nearest + dist;
    }
    return "unpublished";
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
        '<td class="num">' +
        pad2(index + 1) +
        flagHtml(site) +
        '</td><td class="num">' +
        fmtLat(site.lat) +
        '</td><td class="num">' +
        fmtLon(site.lon) +
        '</td><td class="num">' +
        Number(site.total).toFixed(3) +
        '</td><td class="num">' +
        contribCell(contrib.illumination) +
        '</td><td class="num">' +
        contribCell(contrib.hydrogen) +
        '</td><td class="num">' +
        contribCell(contrib.earth_visibility) +
        '</td><td class="num">' +
        stabilityCell(site) +
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

  function renderArtemis(artemis) {
    if (!artemis) {
      els.artemisRail.hidden = true;
      return;
    }
    els.artemisNote.textContent =
      artemis.note || "NASA has not published region centres.";
    els.artemisList.innerHTML = "";
    (artemis.regions || []).forEach(function (region) {
      var li = document.createElement("li");
      li.textContent = region.name;
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

  function currentRequest() {
    return {
      weights: currentWeights(),
      hard: {
        max_slope_deg: Number(els.slope.value),
        slope_radius_m: Number(els.slopeRadius.value),
      },
      n: (meta.defaults && meta.defaults.n) || 10,
    };
  }

  async function downloadRanking(path, filename) {
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
      var label = layer.name === "dem" ? "DEM" : layer.name.replace(/_/g, " ");
      keep += '<option value="' + layer.name + '">' + label + "</option>";
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

  async function runScore() {
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
      setOverlay("data:image/png;base64," + payload.png, payload.bounds, "score");
      els.layer.value = "score";
      selectedRank = null;
      renderSites(payload.sites, request.hard.slope_radius_m);
      renderArtemis(payload.artemis);
      els.csv.disabled = false;
      els.geojson.disabled = false;
    } catch (err) {
      showVeil("Score failed", String(err), "", "");
    } finally {
      els.score.disabled = false;
      els.score.textContent = "Score";
    }
  }

  async function showSingleLayer(name) {
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
      var response = await fetch("/layer/" + encodeURIComponent(name) + ".png");
      if (response.status === 412) {
        var payload = await response.json();
        showNotIngested(detailFrom(response, payload).ingest_command);
        return;
      }
      if (!response.ok) {
        showVeil("Layer missing", "Could not load " + name + ".", "", "");
        return;
      }
      var blob = await response.blob();
      var url = URL.createObjectURL(blob);
      hideVeil();
      setOverlay(url, meta.bounds, name);
    } catch (err) {
      showVeil("Layer missing", String(err), "", "");
    }
  }

  async function toggleArtemis() {
    if (!els.artemis.checked) {
      if (artemisLayer) {
        map.removeLayer(artemisLayer);
        artemisLayer = null;
      }
      els.artemisRail.hidden = true;
      return;
    }
    var response = await fetch("/artemis3.geojson");
    var geojson = await response.json();
    var hasGeom = (geojson.features || []).some(function (feat) {
      return feat.geometry;
    });
    els.artemisRail.hidden = false;
    if (!hasGeom) {
      els.artemisNote.textContent =
        "NASA named nine regions (28 Oct 2024) and did not publish centres. Outlines cannot be drawn.";
      if (!els.artemisList.children.length) {
        (geojson.features || []).forEach(function (feat) {
          var li = document.createElement("li");
          li.textContent = feat.properties && feat.properties.name;
          els.artemisList.appendChild(li);
        });
      }
      return;
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

  function storedPresenting() {
    try {
      return window.localStorage.getItem(PRESENT_KEY) === "1";
    } catch (err) {
      return false;
    }
  }

  async function main() {
    setPresenting(storedPresenting());
    els.present.addEventListener("click", function () {
      setPresenting(!isPresenting());
    });
    [els.illum, els.hydro, els.earth].forEach(function (slider) {
      slider.addEventListener("input", syncSliderLabels);
    });
    els.score.addEventListener("click", runScore);
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

    try {
      var response = await fetch("/layers");
      meta = await response.json();
    } catch (err) {
      showVeil("API unreachable", String(err), "", "");
      return;
    }

    applyDefaults(meta);
    fillLayerSelect(meta);
    initMap(meta);
    await loadAnnotations();

    if (!meta.ingested) {
      showNotIngested(meta.ingest_command);
    } else {
      await runScore();
    }
  }

  main();
})();
