import assert from "node:assert/strict";
import test from "node:test";
import { clockHour, clockTime, formatOffset, inQuietHours, localDay, localIso, localTime, parseIsoWithOffset, shortDate, weekday } from "../src/time.js";

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

test("a time of day is written as the mirror's clock shows it", () => {
  const at = (hour, minute = 0) => Date.UTC(2026, 9, 3, hour + 7, minute);
  assert.equal(clockTime(at(15), -420, false), "3:00 PM");
  assert.equal(clockTime(at(15), -420, true), "15:00");
  assert.equal(clockTime(at(0, 5), -420, false), "12:05 AM");
  assert.equal(clockTime(at(0, 5), -420, true), "00:05");
  assert.equal(clockTime(at(12), -420, false), "12:00 PM");
  assert.equal(clockTime(at(9, 30), -420, false), "9:30 AM");
  assert.equal(clockTime(at(9, 30), -420, true), "09:30");
  assert.equal(clockTime(SATURDAY_0712_PDT, 330, false), "7:42 PM");
  assert.equal(clockHour(at(15), -420, false), "3 PM");
  assert.equal(clockHour(at(0), -420, false), "12 AM");
  assert.equal(clockHour(at(15), -420, true), "15:00");
  assert.equal(shortDate(at(15), -420), "Oct 3");
});

test("two instants are on the same day when the mirror's calendar says so", () => {
  const lateSaturday = Date.UTC(2026, 9, 4, 6, 59);
  const earlySunday = Date.UTC(2026, 9, 4, 7, 0);
  assert.equal(localDay(lateSaturday, -420), localDay(SATURDAY_0712_PDT, -420));
  assert.equal(localDay(earlySunday, -420), localDay(SATURDAY_0712_PDT, -420) + 1);
  // In UTC both are Sunday.
  assert.equal(localDay(lateSaturday, 0), localDay(earlySunday, 0));
});
