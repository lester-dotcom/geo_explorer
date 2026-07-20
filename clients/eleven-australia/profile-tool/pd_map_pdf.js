/* ──────────────────────────────────────────────────────────────────────────
   pd_map_pdf.js — print the current Leaflet view to PDF, for any tool.

   Usage:  PDMapPDF.export(map, { caption: '...' })

   The map container is temporarily moved into a full-page overlay sized to A4
   landscape, rather than hiding page furniture with tool-specific selectors.
   That keeps this module agnostic about each tool's markup.

   Two things that are easy to get wrong and are handled here:

   1. Leaflet positions tiles and markers in pixels for the container size it
      was last told about. If the browser re-lays the page out for paper, the
      printed window lands somewhere else entirely. Pinning the container to
      exact A4 pixels (297×210mm at 96dpi) before printing avoids the mismatch.
   2. fitBounds snaps to whole zoom levels by default, and each level is 2x, so
      the print could show roughly double the area. A fractional zoom is used
      for the export fit so the printed area matches the screen.
   ────────────────────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  var A4_W = 1123, A4_H = 794;   // A4 landscape at 96dpi
  var STYLE_ID = 'pd-map-pdf-style';
  var OVERLAY_ID = 'pd-map-print-overlay';

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = [
      'body.pd-map-only > *:not(#' + OVERLAY_ID + ') { display: none !important; }',
      '#' + OVERLAY_ID + ' { position: fixed; top: 0; left: 0; background: #fff; z-index: 99999;',
      '  width: ' + A4_W + 'px; height: ' + A4_H + 'px; overflow: hidden; }',
      '#' + OVERLAY_ID + ' .leaflet-control-zoom,',
      '#' + OVERLAY_ID + ' .leaflet-control-attribution { display: none !important; }',
      '#pd-map-print-caption { position: absolute; bottom: 0; left: 0; right: 0; z-index: 1000;',
      '  background: rgba(255,255,255,0.92); border-top: 1px solid #e2e0d8;',
      '  padding: 6px 12px; font-size: 11px; line-height: 1.4;',
      '  font-family: "DM Sans", sans-serif; color: #1a1a18; }',
      '@media print {',
      '  @page { size: A4 landscape; margin: 0; }',
      /* Browsers drop background colours when printing, which would render
         coloured markers as empty white circles. */
      '  body.pd-map-only, body.pd-map-only * {',
      '    -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }',
      '  #' + OVERLAY_ID + ' { position: absolute; }',
      '}'
    ].join('\n');
    document.head.appendChild(s);
  }

  /* Find the base tile layer so we can wait for it to finish loading.
     Callers may pass one explicitly via opts.tileLayer. */
  function findTileLayer(map) {
    var found = null;
    try {
      Object.keys(map._layers || {}).forEach(function (k) {
        var l = map._layers[k];
        if (!found && l && typeof l.isLoading === 'function' && l._url) found = l;
      });
    } catch (e) {}
    return found;
  }

  /* Extent of everything plotted on the map — used when the container is
     collapsed and its own bounds cannot be trusted. */
  function boundsOfLayers(map) {
    var b = null;
    function add(latlng) {
      if (!latlng) return;
      b = b ? b.extend(latlng) : L.latLngBounds(latlng, latlng);
    }
    function walk(layer) {
      if (!layer) return;
      if (typeof layer.getLatLng === 'function') { add(layer.getLatLng()); return; }
      if (typeof layer.getBounds === 'function' && layer.getBounds().isValid) {
        var lb = layer.getBounds();
        if (lb.isValid()) { add(lb.getNorthEast()); add(lb.getSouthWest()); }
        return;
      }
      if (typeof layer.eachLayer === 'function') layer.eachLayer(walk);
    }
    try { map.eachLayer(walk); } catch (e) {}
    return b;
  }

  function waitForTiles(layer, timeoutMs) {
    return new Promise(function (resolve) {
      var done = false;
      function finish() {
        if (done) return;
        done = true;
        clearTimeout(timer);
        // Small grace period so the last tiles paint before the dialog opens.
        setTimeout(resolve, 250);
      }
      var timer = setTimeout(finish, timeoutMs || 6000);
      if (!layer) { setTimeout(finish, 600); return; }
      if (typeof layer.isLoading === 'function' && !layer.isLoading()) { finish(); return; }
      layer.once('load', finish);
    });
  }

  /* Print the map's current view. Returns a promise that settles once the UI
     has been restored (or the print dialog has been dismissed). */
  async function exportMap(map, opts) {
    if (!map) return;
    opts = opts || {};
    ensureStyle();

    var container = map.getContainer();
    var parent = container.parentNode;
    var nextSibling = container.nextSibling;
    var prevInlineStyle = container.getAttribute('style');

    // Preserve the geographic area on screen, not the zoom level — the print
    // box is a different shape, so the same zoom would frame a different region.
    var bounds = map.getBounds();
    var screenCentre = map.getCenter(), screenZoom = map.getZoom();

    // A map inside a collapsed panel reports a zero-size container, and its
    // bounds are degenerate — fitting to them would print a meaningless view.
    // Fall back to the extent of the plotted layers in that case.
    var collapsed = container.clientWidth < 10 || container.clientHeight < 10;
    if (collapsed) {
      var layerBounds = boundsOfLayers(map);
      if (layerBounds && layerBounds.isValid()) bounds = layerBounds;
    }

    var overlay = document.createElement('div');
    overlay.id = OVERLAY_ID;
    document.body.appendChild(overlay);
    overlay.appendChild(container);
    document.body.classList.add('pd-map-only');

    container.style.width = A4_W + 'px';
    container.style.height = A4_H + 'px';

    if (opts.caption) {
      var cap = document.createElement('div');
      cap.id = 'pd-map-print-caption';
      cap.textContent = opts.caption + '  ·  Map data © OpenStreetMap contributors';
      overlay.appendChild(cap);
    }

    map.invalidateSize({ animate: false });
    var prevSnap = map.options.zoomSnap;
    map.options.zoomSnap = 0;               // exact fit, no rounding down a level
    map.fitBounds(bounds, { animate: false });
    map.options.zoomSnap = prevSnap;

    await waitForTiles(opts.tileLayer || findTileLayer(map), opts.timeout);

    var restored = false;
    function restore() {
      if (restored) return;
      restored = true;
      window.removeEventListener('afterprint', restore);
      if (prevInlineStyle === null) container.removeAttribute('style');
      else container.setAttribute('style', prevInlineStyle);
      if (nextSibling) parent.insertBefore(container, nextSibling);
      else parent.appendChild(container);
      var o = document.getElementById(OVERLAY_ID);
      if (o) o.remove();
      document.body.classList.remove('pd-map-only');
      map.invalidateSize({ animate: false });
      map.setView(screenCentre, screenZoom, { animate: false });
      if (typeof opts.onRestore === 'function') opts.onRestore();
    }

    window.addEventListener('afterprint', restore);
    window.print();
    setTimeout(restore, 800);   // fallback for browsers that skip afterprint
  }

  window.PDMapPDF = { export: exportMap, A4: { width: A4_W, height: A4_H } };
})();
