// Airtime calculator for sizing a random-schedule test. Pure math, no dependencies.
// LoRa presets confirmed against meshtastic/firmware's MeshRadio.h (modemPresetToParams),
// master branch as of September 2026 -- re-check if Meshtastic changes its presets.
window.MeshCalc = (function () {
  "use strict";

  var PRESETS = {
    SHORT_TURBO: { bw: 500, sf: 7, cr: 5 },
    SHORT_FAST: { bw: 250, sf: 7, cr: 5 },
    SHORT_SLOW: { bw: 250, sf: 8, cr: 5 },
    MEDIUM_FAST: { bw: 250, sf: 9, cr: 5 },
    MEDIUM_SLOW: { bw: 250, sf: 10, cr: 5 },
    LONG_FAST: { bw: 250, sf: 11, cr: 5 },
    LONG_TURBO: { bw: 500, sf: 11, cr: 8 },
    LONG_MODERATE: { bw: 125, sf: 11, cr: 8 },
    LONG_SLOW: { bw: 125, sf: 12, cr: 8 },
  };

  // Rough allowance for Meshtastic's own packet framing and encryption on top of the
  // text (MeshPacket header, nonce/MAC): not verified against the firmware source, a
  // margin on the safe side. Matches PACKET_OVERHEAD_BYTES the bot itself used to add
  // when it still computed this on the Python side.
  var PACKET_OVERHEAD_BYTES = 16;

  // Standard LoRa time-on-air formula (Semtech AN1200.13). `cr` is Meshtastic's own
  // field (5 means 4/5, 8 means 4/8), which already matches the formula's "CR + 4"
  // term. The low-data-rate-optimize bit is guessed from the symbol duration, the
  // same rule of thumb LoRaWAN itself uses (symbol time over 16 ms).
  function airtimeSeconds(bwKhz, sf, cr, payloadBytes) {
    var ts = Math.pow(2, sf) / (bwKhz * 1000);
    var de = ts > 0.016 ? 1 : 0;
    var num = 8 * payloadBytes - 4 * sf + 28 + 16 - 0; // explicit header, CRC on
    var den = 4 * (sf - 2 * de);
    var payloadSymbols = 8 + Math.max(Math.ceil(num / den) * cr, 0);
    var preambleTime = (8 + 4.25) * ts;
    return preambleTime + payloadSymbols * ts;
  }

  function estimate(opts) {
    var perTx = airtimeSeconds(opts.bw, opts.sf, opts.cr, opts.payloadBytes);
    var totalAirtime = opts.participants * opts.messageCount * opts.multiplier * perTx;
    var windowSeconds = opts.windowMinutes * 60;
    var percent = windowSeconds > 0 ? (totalAirtime / windowSeconds) * 100 : Infinity;
    var minutesForTarget = opts.targetPercent > 0 ? totalAirtime / (opts.targetPercent / 100) / 60 : Infinity;
    return { perTx: perTx, totalAirtime: totalAirtime, percent: percent, minutesForTarget: minutesForTarget };
  }

  // Clamp a numeric field to [min, max], falling back to `fallback` if it isn't a
  // finite number (empty, non-numeric, or typed mid-edit) -- unlike a bare
  // `+value || fallback`, this also catches a value that IS a real number but out of
  // range (negative, zero where that makes no physical sense, or absurdly large),
  // which free typing into <input type="number"> is never prevented from producing.
  function clamp(value, min, max, fallback) {
    var v = parseFloat(value);
    if (!isFinite(v)) v = fallback;
    return Math.min(max, Math.max(min, v));
  }

  // Wires a page's form (fixed element ids, shared by every language) to this module,
  // so each page only supplies its own localized strings, not the logic. `strings`:
  // {place: "City", of: fn(windowMinutes, totalAirtimeSeconds, perTxSeconds) -> str,
  //  target: fn(targetPercent, minutesNeeded) -> str (may include markup)}.
  function wireForm(strings) {
    var $ = function (id) { return document.getElementById(id); };
    var modeSel = $("calcMode"), customFields = $("calcCustomFields"), result = $("calcResult");

    function updateCustomVisibility() {
      customFields.hidden = modeSel.value !== "custom";
    }

    function currentLora() {
      if (modeSel.value === "custom") {
        return {
          bw: clamp($("calcBw").value, 10, 1000, 250),
          sf: clamp($("calcSf").value, 7, 12, 11),
          cr: clamp($("calcCr").value, 5, 8, 5),
          name: "CUSTOM",
        };
      }
      var p = PRESETS[modeSel.value];
      return { bw: p.bw, sf: p.sf, cr: p.cr, name: modeSel.value };
    }

    function recalc() {
      var participants = clamp($("calcParticipants").value, 1, 5000, 1);
      var count = clamp($("calcCount").value, 1, 50, 1);
      var multiplier = parseFloat($("calcMultiplier").value);
      var windowMinutes = clamp($("calcWindow").value, 0.1, 10080, 120);
      var targetPercent = clamp($("calcTarget").value, 1, 100, 10);
      var lora = currentLora();
      var msg = "MTBOT " + lora.name + " | " + strings.place + " | " + count + "/" + count;
      var payload = new TextEncoder().encode(msg).length + PACKET_OVERHEAD_BYTES;
      var r = estimate({
        bw: lora.bw, sf: lora.sf, cr: lora.cr, payloadBytes: payload,
        participants: participants, messageCount: count, multiplier: multiplier,
        windowMinutes: windowMinutes, targetPercent: targetPercent,
      });
      var over = r.percent > targetPercent;
      result.className = "calc-result" + (over ? " over" : "");
      result.innerHTML =
        '<div class="big">' + r.percent.toFixed(1) + "%</div>" +
        "<p>" + strings.of(windowMinutes, Math.round(r.totalAirtime), r.perTx.toFixed(2)) + "</p>" +
        "<p>" + strings.target(targetPercent, Math.ceil(r.minutesForTarget)) + "</p>";
    }

    modeSel.addEventListener("change", function () { updateCustomVisibility(); recalc(); });
    ["calcParticipants", "calcCount", "calcMultiplier", "calcWindow", "calcTarget", "calcBw", "calcSf", "calcCr"]
      .forEach(function (id) { $(id).addEventListener("input", recalc); });
    updateCustomVisibility();
    recalc();
  }

  return { PRESETS: PRESETS, airtimeSeconds: airtimeSeconds, estimate: estimate, clamp: clamp, wireForm: wireForm };
})();
