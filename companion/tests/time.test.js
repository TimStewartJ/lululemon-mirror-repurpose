import assert from "node:assert/strict";
import test from "node:test";
import { formatOffset, inQuietHours, localIso, localTime, parseIsoWithOffset, weekday } from "../src/time.js";

const SATURDAY_0712_PDT = Date.UTC(2026, 9, 3, 14, 12, 5);

test("local time is the mirror's wall clock with its offset", () => {
  assert.equal(localIso(SATURDAY_0712_PDT, -420), "2026-10-03T07:12:05-07:00");
  assert.equal(localIso(SATURDAY_0712_PDT, 330), "2026-10-03T19:42:05+05:30");
  assert.equal(localIso(SATURDAY_0712_PDT, 0), "2026-10-03T14:12:05+00:00");
  assert.equal(localTime(SATURDAY_0712_PDT, -420), "07:12");
  assert.equal(formatOffset(-570), "-09:30");
});

test("the weekday is the mirror's, not UTC's", () => {
  // 23:30 on Saturday in Los Angeles is already Sunday in UTC.
  const lateSaturday = Date.UTC(2026, 9, 4, 6, 30);
  assert.equal(weekday(lateSaturday, -420), "Saturday");
  assert.equal(weekday(lateSaturday, 0), "Sunday");
});

test("an ISO time with an offset becomes the right instant", () => {
  assert.deepEqual(parseIsoWithOffset("2026-10-04T07:00:00-07:00"), { ms: Date.UTC(2026, 9, 4, 14, 0, 0) });
  assert.deepEqual(parseIsoWithOffset("2026-10-04T14:00:00Z"), { ms: Date.UTC(2026, 9, 4, 14, 0, 0) });
  assert.deepEqual(parseIsoWithOffset("2026-10-04T07:00-0700"), { ms: Date.UTC(2026, 9, 4, 14, 0, 0) });
  assert.deepEqual(parseIsoWithOffset(" 2026-10-04 19:30:00+05:30 "), { ms: Date.UTC(2026, 9, 4, 14, 0, 0) });
  assert.deepEqual(parseIsoWithOffset("2026-10-04T07:00:00.250-07:00"), { ms: Date.UTC(2026, 9, 4, 14, 0, 0) });
});

test("a time without an offset, a wrong form and an impossible date are told apart", () => {
  assert.deepEqual(parseIsoWithOffset("2026-10-04T07:00:00"), { problem: "offset" });
  assert.deepEqual(parseIsoWithOffset("tomorrow at seven"), { problem: "format" });
  assert.deepEqual(parseIsoWithOffset(1791000000000), { problem: "format" });
  assert.deepEqual(parseIsoWithOffset("2026-02-30T07:00:00Z"), { problem: "impossible" });
  assert.deepEqual(parseIsoWithOffset("2026-10-04T25:00:00Z"), { problem: "impossible" });
  assert.deepEqual(parseIsoWithOffset("2026-10-04T07:00:00+15:00"), { problem: "impossible" });
});

test("quiet hours run past midnight in the mirror's zone", () => {
  const at = (hour, minute) => Date.UTC(2026, 9, 3, hour + 7, minute);
  const quiet = ["22:30", "06:30"];
  assert.equal(inQuietHours(at(22, 29), -420, quiet), false);
  assert.equal(inQuietHours(at(22, 30), -420, quiet), true);
  assert.equal(inQuietHours(at(3, 0), -420, quiet), true);
  assert.equal(inQuietHours(at(6, 29), -420, quiet), true);
  assert.equal(inQuietHours(at(6, 30), -420, quiet), false);
  assert.equal(inQuietHours(at(12, 0), -420, ["09:00", "17:00"]), true);
  assert.equal(inQuietHours(at(3, 0), -420, null), false);
});
