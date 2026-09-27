// Tests for docs/assets/calculator.js. No dependencies: Node's own test runner.
// Run: node --test tests/test_calculator.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

// calculator.js is a plain browser script that assigns to `window.MeshCalc`; give it
// a `window` that is just the global object, so requiring it here works unchanged.
global.window = global;
require(path.join(__dirname, "..", "docs", "assets", "calculator.js"));
const MeshCalc = global.MeshCalc;

test("a hand-computed example matches the formula (SF7, BW125, CR 4/5, 10-byte payload)", () => {
  // Semtech's own worked example: close to 41.2 ms.
  assert.ok(Math.abs(MeshCalc.airtimeSeconds(125, 7, 5, 10) - 0.041216) < 0.0005);
});

test("a higher spreading factor takes longer", () => {
  const fast = MeshCalc.airtimeSeconds(125, 7, 5, 20);
  const slow = MeshCalc.airtimeSeconds(125, 10, 5, 20);
  assert.ok(slow > fast);
});

test("a narrower bandwidth takes longer", () => {
  const wide = MeshCalc.airtimeSeconds(250, 9, 5, 20);
  const narrow = MeshCalc.airtimeSeconds(62, 9, 5, 20);
  assert.ok(narrow > wide);
});

test("a bigger payload takes longer", () => {
  const short = MeshCalc.airtimeSeconds(125, 9, 5, 10);
  const long = MeshCalc.airtimeSeconds(125, 9, 5, 100);
  assert.ok(long > short);
});

test("every shipped preset produces a finite, positive airtime", () => {
  for (const [name, p] of Object.entries(MeshCalc.PRESETS)) {
    const t = MeshCalc.airtimeSeconds(p.bw, p.sf, p.cr, 40);
    assert.ok(Number.isFinite(t) && t > 0, `${name}: ${t}`);
  }
});

test("estimate() combines participants, message count and multiplier linearly", () => {
  const base = { bw: 250, sf: 11, cr: 5, payloadBytes: 40, multiplier: 3, windowMinutes: 120, targetPercent: 10 };
  const ten = MeshCalc.estimate({ ...base, participants: 10, messageCount: 3 });
  const twenty = MeshCalc.estimate({ ...base, participants: 20, messageCount: 3 });
  assert.ok(Math.abs(twenty.totalAirtime - 2 * ten.totalAirtime) < 1e-9);
  assert.ok(Math.abs(twenty.percent - 2 * ten.percent) < 1e-9);
});

test("estimate()'s minutesForTarget is the window where percent would equal targetPercent", () => {
  const r = MeshCalc.estimate({
    bw: 250, sf: 11, cr: 5, payloadBytes: 40, multiplier: 3,
    participants: 100, messageCount: 5, windowMinutes: 120, targetPercent: 10,
  });
  const atThatWindow = MeshCalc.estimate({
    bw: 250, sf: 11, cr: 5, payloadBytes: 40, multiplier: 3,
    participants: 100, messageCount: 5, windowMinutes: r.minutesForTarget, targetPercent: 10,
  });
  assert.ok(Math.abs(atThatWindow.percent - 10) < 1e-6);
});

test("clamp() leaves an in-range value alone", () => {
  assert.equal(MeshCalc.clamp("42", 1, 100, 0), 42);
});

test("clamp() floors a value below the minimum, including negative input", () => {
  // This is the bug the code review caught: the custom BW/SF/CR fields used to fall
  // through to the formula unclamped, so a negative or below-range value produced a
  // nonsensical (even negative) airtime instead of being floored like every other field.
  assert.equal(MeshCalc.clamp("-250", 10, 1000, 250), 10);
  assert.equal(MeshCalc.clamp("2", 7, 12, 11), 7);
});

test("clamp() ceils a value above the maximum", () => {
  assert.equal(MeshCalc.clamp("9999", 10, 1000, 250), 1000);
});

test("clamp() falls back on empty or non-numeric input", () => {
  assert.equal(MeshCalc.clamp("", 1, 100, 42), 42);
  assert.equal(MeshCalc.clamp("abc", 1, 100, 42), 42);
});
