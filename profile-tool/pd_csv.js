/* ──────────────────────────────────────────────────────────────────────────
   pd_csv.js — shared CSV parsing + column detection for the Geo Intelligence Suite.

   Replaces the per-tool `line.split(',')` parsers, which corrupt any row with a
   quoted comma (e.g. a net-sales column of "£1,358").

   Columns are detected by sniffing the values, with header text only as a
   tie-breaker, so a client CSV needs no configuration: whichever column holds
   real postcodes wins, whichever holds money becomes the value, and so on.

   Produces records of the shape:
     { postcode, name, value, valueRaw, group, ref }
   where `value` is a Number (or null) and the rest are strings (or null).
   Tools spread these into their result rows, so the extra fields survive
   geocoding and reach popups, exports and briefs automatically.
   ────────────────────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  var FULL_PC = /^[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}$/i;
  var PC_IN_TEXT = /([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})/i;

  // ── Primitives ───────────────────────────────────────────────────────────

  function normalisePC(s) {
    if (!s) return null;
    var c = String(s).replace(/\s+/g, '').toUpperCase();
    if (c.length >= 5 && c.length <= 7) return c.slice(0, -3) + ' ' + c.slice(-3);
    return null;
  }

  function extractPC(s) {
    if (!s) return null;
    var m = String(s).match(PC_IN_TEXT);
    return m ? normalisePC(m[1]) : null;
  }

  function pcKey(pc) {
    return String(pc || '').replace(/\s+/g, '').toUpperCase();
  }

  /* Parse a money-ish string into a Number. Handles currency symbols, thousands
     separators, trailing/leading whitespace, and (1,234) negative notation. */
  function parseMoney(s) {
    if (s == null || s === '') return null;
    var t = String(s).trim();
    if (!t) return null;
    var neg = /^\(.*\)$/.test(t) || /^-/.test(t);
    t = t.replace(/[()\-]/g, '').replace(/[£$€,\s]/g, '');
    if (!t || !/^\d*\.?\d+$/.test(t)) return null;
    var n = parseFloat(t);
    if (!isFinite(n)) return null;
    return neg ? -n : n;
  }

  function looksLikeMoney(s) {
    if (s == null || String(s).trim() === '') return false;
    // Require a currency marker, a thousands separator, or a plain number —
    // but reject bare codes like "TLH01" that parseMoney would also reject.
    return /^[\(\-]?\s*[£$€]?\s*[\d,]+(\.\d+)?\s*\)?$/.test(String(s).trim())
      && parseMoney(s) !== null;
  }

  // ── RFC 4180 parser ──────────────────────────────────────────────────────

  /* Split raw CSV text into an array of string arrays. Handles quoted fields,
     escaped quotes (""), embedded commas and newlines, and CRLF. Falls back to
     tab or semicolon delimiters when the first line clearly uses one. */
  function parseRows(text) {
    if (!text) return [];
    var src = String(text).replace(/^﻿/, '');
    var delim = sniffDelimiter(src);
    var rows = [];
    var row = [];
    var field = '';
    var inQuotes = false;
    var i = 0;

    while (i < src.length) {
      var ch = src[i];

      if (inQuotes) {
        if (ch === '"') {
          if (src[i + 1] === '"') { field += '"'; i += 2; continue; }
          inQuotes = false; i++; continue;
        }
        field += ch; i++; continue;
      }

      if (ch === '"') { inQuotes = true; i++; continue; }
      if (ch === delim) { row.push(field); field = ''; i++; continue; }
      if (ch === '\r') { i++; continue; }
      if (ch === '\n') { row.push(field); rows.push(row); row = []; field = ''; i++; continue; }
      field += ch; i++;
    }
    row.push(field);
    rows.push(row);

    return rows
      .map(function (r) { return r.map(function (c) { return c.trim(); }); })
      .filter(function (r) { return r.some(function (c) { return c !== ''; }); });
  }

  function sniffDelimiter(src) {
    var firstLine = src.split(/\r?\n/)[0] || '';
    var counts = { ',': 0, '\t': 0, ';': 0 };
    var inQ = false;
    for (var i = 0; i < firstLine.length; i++) {
      var ch = firstLine[i];
      if (ch === '"') { inQ = !inQ; continue; }
      if (!inQ && counts[ch] !== undefined) counts[ch]++;
    }
    var best = ',';
    Object.keys(counts).forEach(function (d) { if (counts[d] > counts[best]) best = d; });
    return best;
  }

  // ── Column detection ─────────────────────────────────────────────────────

  var HEADER_HINTS = {
    postcode: /post\s*code|postal|zip/i,
    name:     /salon|store|branch|outlet|practice|stockist|client|customer|account\s*name|company|business|\bname\b/i,
    value:    /sales|revenue|retail|turnover|spend|value|amount|total|net|gross|£|\$|€/i,
    group:    /^\s*(am|acc(ount)?\s*manager|manager|rep|bdm|owner|territory|agent|area\s*manager|sales\s*rep)\s*$/i,
    ref:      /acc(ount)?\s*(code|no|num|number|id)|^\s*(code|id|ref|reference)\s*$/i
  };

  function isHeaderRow(cells) {
    // A header row has no full postcode in it and no money-looking cell.
    var hasPC = cells.some(function (c) { return FULL_PC.test(c.replace(/\s+/g, ' ').trim()); });
    if (hasPC) return false;
    var hinted = cells.some(function (c) {
      return HEADER_HINTS.postcode.test(c) || HEADER_HINTS.name.test(c) ||
             HEADER_HINTS.value.test(c) || HEADER_HINTS.group.test(c);
    });
    return hinted;
  }

  function columnStats(rows, colCount) {
    var stats = [];
    for (var c = 0; c < colCount; c++) {
      var pc = 0, money = 0, filled = 0, distinct = {}, lenSum = 0;
      for (var r = 0; r < rows.length; r++) {
        var v = rows[r][c];
        if (v == null || v === '') continue;
        filled++;
        lenSum += v.length;
        distinct[v] = 1;
        if (FULL_PC.test(v)) pc++;
        if (looksLikeMoney(v)) money++;
      }
      stats.push({
        index: c,
        filled: filled,
        pcRatio: filled ? pc / filled : 0,
        moneyRatio: filled ? money / filled : 0,
        distinct: Object.keys(distinct).length,
        avgLen: filled ? lenSum / filled : 0
      });
    }
    return stats;
  }

  /* Decide which column is which. Value sniffing leads; headers break ties.
     Each column is claimed at most once. */
  function detectColumns(headers, rows) {
    var colCount = 0;
    rows.forEach(function (r) { colCount = Math.max(colCount, r.length); });
    headers = headers || [];
    var stats = columnStats(rows, colCount);
    var taken = {};
    var out = { postcode: -1, name: -1, value: -1, group: -1, ref: -1 };

    function hdr(i) { return headers[i] || ''; }
    function claim(field, idx) {
      if (idx == null || idx < 0) return;
      out[field] = idx;
      taken[idx] = 1;
    }
    function free(s) { return !taken[s.index]; }

    // Postcode: the column that actually holds postcodes.
    var pcCands = stats.filter(free).filter(function (s) { return s.pcRatio >= 0.5; });
    pcCands.sort(function (a, b) {
      var d = b.pcRatio - a.pcRatio;
      if (d) return d;
      return (HEADER_HINTS.postcode.test(hdr(b.index)) ? 1 : 0) -
             (HEADER_HINTS.postcode.test(hdr(a.index)) ? 1 : 0);
    });
    if (pcCands.length) claim('postcode', pcCands[0].index);

    // Value: money-looking column, preferring a revenue-ish header.
    var valCands = stats.filter(free).filter(function (s) { return s.moneyRatio >= 0.6; });
    valCands.sort(function (a, b) {
      var ha = HEADER_HINTS.value.test(hdr(a.index)) ? 1 : 0;
      var hb = HEADER_HINTS.value.test(hdr(b.index)) ? 1 : 0;
      if (ha !== hb) return hb - ha;
      return b.moneyRatio - a.moneyRatio;
    });
    if (valCands.length && HEADER_HINTS.value.test(hdr(valCands[0].index))) {
      claim('value', valCands[0].index);
    }

    // Group: low-cardinality text column with a manager-ish header.
    var grpCands = stats.filter(free).filter(function (s) {
      return HEADER_HINTS.group.test(hdr(s.index)) ||
             (s.filled > 0 && s.distinct > 1 && s.distinct <= Math.max(2, s.filled * 0.25) && s.avgLen <= 40);
    });
    grpCands.sort(function (a, b) {
      var ha = HEADER_HINTS.group.test(hdr(a.index)) ? 1 : 0;
      var hb = HEADER_HINTS.group.test(hdr(b.index)) ? 1 : 0;
      if (ha !== hb) return hb - ha;
      return a.distinct - b.distinct;
    });
    if (grpCands.length && HEADER_HINTS.group.test(hdr(grpCands[0].index))) {
      claim('group', grpCands[0].index);
    }

    // Name: header hint first, else the widest free text column.
    var nameCands = stats.filter(free).filter(function (s) { return s.pcRatio < 0.5 && s.moneyRatio < 0.6; });
    var namedHit = nameCands.filter(function (s) { return HEADER_HINTS.name.test(hdr(s.index)); });
    if (namedHit.length) {
      namedHit.sort(function (a, b) { return b.avgLen - a.avgLen; });
      claim('name', namedHit[0].index);
    }

    // Ref: header hint only — too easy to grab an arbitrary code column otherwise.
    var refHit = stats.filter(free).filter(function (s) { return HEADER_HINTS.ref.test(hdr(s.index)); });
    if (refHit.length) claim('ref', refHit[0].index);

    return out;
  }

  // ── Public entry point ───────────────────────────────────────────────────

  /* Parse CSV text into enriched records.
     Returns { records, columns, headers, hasName, hasValue, hasGroup, valueLabel, groupLabel }. */
  function records(text) {
    var rows = parseRows(text);
    var empty = {
      records: [], columns: {}, headers: [],
      hasName: false, hasValue: false, hasGroup: false,
      valueLabel: 'Value', groupLabel: 'Group', nameLabel: 'Name'
    };
    if (!rows.length) return empty;

    var headers = [];
    var dataRows = rows;
    if (isHeaderRow(rows[0])) { headers = rows[0]; dataRows = rows.slice(1); }
    if (!dataRows.length) return empty;

    var cols = detectColumns(headers, dataRows);
    var recs = [];

    for (var i = 0; i < dataRows.length; i++) {
      var parts = dataRows[i];
      var pc = null;

      if (cols.postcode >= 0) pc = normalisePC(parts[cols.postcode]) || extractPC(parts[cols.postcode]);
      if (!pc) {
        // Fall back to the old behaviour: first cell containing a postcode.
        for (var j = 0; j < parts.length && !pc; j++) pc = extractPC(parts[j]);
      }
      if (!pc) continue;

      var raw = cols.value >= 0 ? parts[cols.value] : null;
      recs.push({
        postcode: pc,
        name:     cols.name  >= 0 ? (parts[cols.name] || null)  : null,
        group:    cols.group >= 0 ? (parts[cols.group] || null) : null,
        ref:      cols.ref   >= 0 ? (parts[cols.ref] || null)   : null,
        value:    raw != null ? parseMoney(raw) : null,
        valueRaw: raw || null
      });
    }

    return {
      records: recs,
      columns: cols,
      headers: headers,
      hasName:  cols.name >= 0  && recs.some(function (r) { return r.name; }),
      hasValue: cols.value >= 0 && recs.some(function (r) { return r.value != null; }),
      hasGroup: cols.group >= 0 && recs.some(function (r) { return r.group; }),
      nameLabel:  cols.name >= 0  ? (headers[cols.name] || 'Name')   : 'Name',
      valueLabel: cols.value >= 0 ? (headers[cols.value] || 'Value') : 'Value',
      groupLabel: cols.group >= 0 ? (headers[cols.group] || 'Group') : 'Group'
    };
  }

  // ── Helpers for tools ────────────────────────────────────────────────────

  /* Rebuild a meta object from records alone.

     Client builds restore saved datasets from localStorage, where the original
     parse metadata may be absent. Without this, a tool would read
     `meta.valueLabel` off null. Column labels can't be recovered from the
     records, so generic ones are used unless `saved` supplies the originals. */
  function metaFromRecords(recs, saved) {
    recs = recs || [];
    if (saved && saved.columns) return saved;
    var hasName  = recs.some(function (r) { return r && r.name; });
    var hasValue = recs.some(function (r) { return r && r.value != null; });
    var hasGroup = recs.some(function (r) { return r && r.group; });
    var hasRef   = recs.some(function (r) { return r && r.ref; });
    return {
      records: recs,
      columns: { postcode: 0, name: hasName ? 1 : -1, value: hasValue ? 2 : -1,
                 group: hasGroup ? 3 : -1, ref: hasRef ? 4 : -1 },
      headers: [],
      hasName: hasName, hasValue: hasValue, hasGroup: hasGroup,
      nameLabel: 'Name', valueLabel: 'Value', groupLabel: 'Group'
    };
  }

  function groupsOf(recs) {
    var seen = {};
    (recs || []).forEach(function (r) { if (r && r.group) seen[r.group] = 1; });
    return Object.keys(seen).sort();
  }

  function sumValue(recs) {
    return (recs || []).reduce(function (a, r) { return a + (r && r.value != null ? r.value : 0); }, 0);
  }

  function fmtValue(n) {
    if (n == null || !isFinite(n)) return '';
    return '£' + Math.round(n).toLocaleString('en-GB');
  }

  /* Index records by normalised postcode. Postcodes can repeat (two salons at
     one postcode), so every key maps to an array. */
  function byPostcode(recs) {
    var m = {};
    (recs || []).forEach(function (r) {
      if (!r || !r.postcode) return;
      var k = pcKey(r.postcode);
      (m[k] = m[k] || []).push(r);
    });
    return m;
  }

  /* Escape one CSV field: quote it if it contains a delimiter, quote or newline. */
  function csvCell(v) {
    var s = (v == null ? '' : String(v));
    if (/[",\n\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  /* Serialise an array of arrays into CSV text, quoting where required. */
  function toCSV(rows) {
    return (rows || []).map(function (r) {
      return (r || []).map(csvCell).join(',');
    }).join('\n');
  }

  window.PDCSV = {
    parseRows: parseRows,
    records: records,
    metaFromRecords: metaFromRecords,
    detectColumns: detectColumns,
    normalisePC: normalisePC,
    extractPC: extractPC,
    pcKey: pcKey,
    parseMoney: parseMoney,
    groupsOf: groupsOf,
    sumValue: sumValue,
    fmtValue: fmtValue,
    byPostcode: byPostcode,
    csvCell: csvCell,
    toCSV: toCSV
  };
})();
