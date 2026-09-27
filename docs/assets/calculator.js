// Airtime calculator for sizing a random-schedule test. Pure math, no dependencies.
// LoRa presets confirmed against meshtastic/firmware's MeshRadio.h (modemPresetToParams).
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

  return { PRESETS: PRESETS, airtimeSeconds: airtimeSeconds, estimate: estimate };
})();
