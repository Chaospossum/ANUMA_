(function () {
  "use strict";

  var PROJ4 = "+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=m +no_defs";
  var TICKS = 10;

  var map = null;
  var overlay = null;
  var siteLayer = null;
  var artemisLayer = null;
  var meta = null;
  var lastScore = null;
  var overlayUrl = null;
  var selectedRank = null;

  var els = {
    illum: document.getElementById("w-illum"),
    hydro: document.getElementById("w-hydro"),
    earth: document.getElementById("w-earth"),
    illumVal: document.getElementById("w-illum-val"),
    hydroVal: document.getElementById("w-hydro-val"),
    earthVal: document.getElementById("w-earth-val"),
    slope: document.getElementById("max-slope"),
    score: document.getElementById("btn-score"),
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

  function emptySites(message) {
    els.sites.innerHTML =
      '<tr><td class="empty" colspan="8">' + message + "</td></tr>";
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

  function setOverlay(url, bounds) {
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

  function ticksHtml(value, weight) {
    var frac = weight > 0 ? value / weight : 0;
    var nOn = Math.round(Math.max(0, Math.min(1, frac)) * TICKS);
    var html = '<span class="ticks">';
    var i;
    for (i = 0; i < TICKS; i += 1) {
      html += '<span class="tick' + (i < nOn ? " on" : "") + '"></span>';
    }
    return html + "</span>";
  }

  function artemisCell(site) {
    if (site.nearest) {
      var dist =
        site.distance_km == null ? "" : " (" + Number(site.distance_km).toFixed(1) + " km)";
      return site.nearest + dist;
    }
    return "unpublished";
  }

  function renderSites(sites) {
    els.sites.innerHTML = "";
    if (!sites || !sites.length) {
      emptySites("No sites passed the slope mask.");
      return;
    }
    if (siteLayer) {
      map.removeLayer(siteLayer);
    }
    siteLayer = L.layerGroup().addTo(map);
    var weights = currentWeights();
    sites.forEach(function (site, index) {
      var contrib = site.contributions || {};
      var tr = document.createElement("tr");
      if (selectedRank === index) {
        tr.className = "active";
      }
      tr.innerHTML =
        '<td class="num">' +
        pad2(index + 1) +
        '</td><td class="num">' +
        fmtLat(site.lat) +
        '</td><td class="num">' +
        fmtLon(site.lon) +
        '</td><td class="num">' +
        Number(site.total).toFixed(3) +
        "</td><td>" +
        ticksHtml(contrib.illumination || 0, weights.illumination || 1) +
        "</td><td>" +
        ticksHtml(contrib.hydrogen || 0, weights.hydrogen || 1) +
        "</td><td>" +
        ticksHtml(contrib.earth_visibility || 0, weights.earth_visibility || 1) +
        "</td><td>" +
        artemisCell(site) +
        "</td>";
      tr.addEventListener("click", function () {
        selectedRank = index;
        map.setView([site.lat, site.lon], Math.max(map.getZoom(), 4));
        renderSites(sites);
      });
      els.sites.appendChild(tr);

      L.marker([site.lat, site.lon], {
        icon: L.divIcon({
          className: "site-mark",
          html: "<span>" + pad2(index + 1) + "</span>",
          iconSize: [22, 14],
          iconAnchor: [11, 7],
        }),
      })
        .on("click", function () {
          selectedRank = index;
          map.setView([site.lat, site.lon], Math.max(map.getZoom(), 4));
          renderSites(sites);
        })
        .addTo(siteLayer);
    });
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
    els.score.textContent = "Scoring";
    try {
      var response = await fetch("/score", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          weights: currentWeights(),
          hard: { max_slope_deg: Number(els.slope.value) },
          n: (meta.defaults && meta.defaults.n) || 10,
        }),
      });
      var payload = await response.json();
      if (response.status === 412) {
        var detail = detailFrom(response, payload);
        showNotIngested(detail.ingest_command);
        return;
      }
      if (!response.ok) {
        showVeil(
          "Score failed",
          payload.detail || payload.message || "The API returned an error.",
          "",
          ""
        );
        return;
      }
      lastScore = payload;
      hideVeil();
      setOverlay("data:image/png;base64," + payload.png, payload.bounds);
      els.layer.value = "score";
      selectedRank = null;
      renderSites(payload.sites);
      renderArtemis(payload.artemis);
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
        setOverlay("data:image/png;base64," + lastScore.png, lastScore.bounds);
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
      setOverlay(url, meta.bounds);
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

  async function main() {
    [els.illum, els.hydro, els.earth].forEach(function (slider) {
      slider.addEventListener("input", syncSliderLabels);
    });
    els.score.addEventListener("click", runScore);
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

    if (!meta.ingested) {
      showNotIngested(meta.ingest_command);
    } else {
      await runScore();
    }
  }

  main();
})();
