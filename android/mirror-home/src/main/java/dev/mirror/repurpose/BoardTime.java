package dev.mirror.repurpose;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/* Times on the board. The API answers in epoch milliseconds like the rest of
   the control API, and accepts those or an ISO 8601 time with its offset,
   which is what a program composing a request from a sentence has to hand.
   Android 6 has no java.time, so the calendar arithmetic is done here. */
public final class BoardTime {
    public static final String EXAMPLE = "2026-10-03T09:00:00-07:00";
    static final long EARLIEST = 1577836800000L; // 2020-01-01T00:00:00Z
    static final long LATEST = 4102444800000L; // 2100-01-01T00:00:00Z
    /* Below this a number can only be seconds: it is the year 1973 in
       milliseconds and the year 5138 in seconds. */
    private static final long SMALLEST_MILLISECONDS = 100_000_000_000L;
    private static final Pattern ISO = Pattern.compile(
            "(\\d{4})-(\\d{2})-(\\d{2})[Tt ](\\d{2}):(\\d{2})(?::(\\d{2})(?:[.,](\\d{1,9}))?)?"
                    + "\\s?([Zz]|[+-]\\d{2}(?::?\\d{2})?)?");
    private static final int[] MONTH_DAYS = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};

    private BoardTime() {
    }

    /** Epoch milliseconds from a JSON number or an ISO 8601 string with an offset. */
    public static long parse(Object value, String field) {
        if (value instanceof Number) {
            double number = ((Number) value).doubleValue();
            if (Double.isNaN(number) || Double.isInfinite(number) || number != Math.rint(number)) {
                throw BoardError.invalid(field, field + " must be a whole number of milliseconds");
            }
            long millis = (long) number;
            if (millis > 0 && millis < SMALLEST_MILLISECONDS) {
                throw BoardError.invalid(
                        field,
                        field + " looks like seconds. Give milliseconds since 1970, or an ISO 8601 "
                                + "time such as " + EXAMPLE);
            }
            return inRange(millis, field);
        }
        if (value instanceof String) {
            return inRange(parseIso(((String) value).trim(), field), field);
        }
        throw BoardError.invalid(field, field + " must be a time: " + formats());
    }

    /** The instant as ISO 8601 in UTC, to the second. */
    public static String iso(long millis) {
        long days = floorDiv(millis, 86_400_000L);
        long rest = millis - days * 86_400_000L;
        // Days to a civil date, after Howard Hinnant's algorithm.
        long shifted = days + 719_468L;
        long era = floorDiv(shifted, 146_097L);
        long dayOfEra = shifted - era * 146_097L;
        long yearOfEra =
                (dayOfEra - dayOfEra / 1460 + dayOfEra / 36_524 - dayOfEra / 146_096) / 365;
        long dayOfYear = dayOfEra - (365 * yearOfEra + yearOfEra / 4 - yearOfEra / 100);
        long monthIndex = (5 * dayOfYear + 2) / 153;
        long day = dayOfYear - (153 * monthIndex + 2) / 5 + 1;
        long month = monthIndex < 10 ? monthIndex + 3 : monthIndex - 9;
        long year = yearOfEra + era * 400 + (month <= 2 ? 1 : 0);
        return String.format(
                Locale.US,
                "%04d-%02d-%02dT%02d:%02d:%02dZ",
                year,
                month,
                day,
                rest / 3_600_000L,
                rest / 60_000L % 60,
                rest / 1000L % 60);
    }

    static String formats() {
        return "milliseconds since 1970, or ISO 8601 with an offset such as " + EXAMPLE;
    }

    private static long parseIso(String text, String field) {
        Matcher matcher = ISO.matcher(text);
        if (!matcher.matches()) {
            throw BoardError.invalid(
                    field, field + " is not a time the Mirror can read. Use " + formats());
        }
        String zone = matcher.group(8);
        if (zone == null) {
            throw BoardError.invalid(
                    field,
                    field + " needs its offset from UTC, for example " + EXAMPLE
                            + " or 2026-10-03T16:00:00Z");
        }
        int year = Integer.parseInt(matcher.group(1));
        int month = Integer.parseInt(matcher.group(2));
        int day = Integer.parseInt(matcher.group(3));
        int hour = Integer.parseInt(matcher.group(4));
        int minute = Integer.parseInt(matcher.group(5));
        int second = matcher.group(6) == null ? 0 : Integer.parseInt(matcher.group(6));
        if (month < 1 || month > 12 || day < 1 || day > daysIn(year, month)
                || hour > 23 || minute > 59 || second > 59) {
            throw BoardError.invalid(field, field + " is not a real date and time: " + text);
        }
        long millis = 0;
        if (matcher.group(7) != null) {
            String fraction = (matcher.group(7) + "00").substring(0, 3);
            millis = Long.parseLong(fraction);
        }
        long offsetMinutes = 0;
        if (zone.length() > 1) {
            String digits = zone.substring(1).replace(":", "");
            int offsetHours = Integer.parseInt(digits.substring(0, 2));
            int offsetRest = digits.length() > 2 ? Integer.parseInt(digits.substring(2)) : 0;
            if (offsetHours > 14 || offsetRest > 59) {
                throw BoardError.invalid(field, field + " has an impossible UTC offset: " + zone);
            }
            offsetMinutes = (offsetHours * 60L + offsetRest) * (zone.charAt(0) == '-' ? -1 : 1);
        }
        return daysFromCivil(year, month, day) * 86_400_000L
                + hour * 3_600_000L
                + minute * 60_000L
                + second * 1000L
                + millis
                - offsetMinutes * 60_000L;
    }

    private static long inRange(long millis, String field) {
        if (millis < EARLIEST || millis >= LATEST) {
            throw BoardError.invalid(field, field + " must fall between 2020 and 2100");
        }
        return millis;
    }

    private static int daysIn(int year, int month) {
        boolean leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        return month == 2 && leap ? 29 : MONTH_DAYS[month - 1];
    }

    private static long daysFromCivil(int year, int month, int day) {
        long adjusted = month <= 2 ? year - 1L : year;
        long era = floorDiv(adjusted, 400L);
        long yearOfEra = adjusted - era * 400L;
        long dayOfYear = (153L * (month > 2 ? month - 3 : month + 9) + 2) / 5 + day - 1;
        long dayOfEra = yearOfEra * 365L + yearOfEra / 4 - yearOfEra / 100 + dayOfYear;
        return era * 146_097L + dayOfEra - 719_468L;
    }

    /* Math.floorDiv arrived with Android 7. */
    private static long floorDiv(long value, long divisor) {
        long quotient = value / divisor;
        return value % divisor != 0 && (value < 0) != (divisor < 0) ? quotient - 1 : quotient;
    }
}
