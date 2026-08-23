/**
 * HTTP Range header parsing for single-range byte requests, as used by
 * the media library's file-serving endpoint. Multi-range requests
 * (e.g. "bytes=0-10,20-30") are intentionally not supported; callers
 * should treat that shape as unsatisfiable/unsupported and fall back to a
 * full response or a 416, per RFC 7233's allowance for servers to ignore
 * the Range header entirely when it cannot be honored.
 */

export interface ByteRange {
  start: number;
  end: number; // inclusive
}

export type RangeParseResult =
  | { kind: 'none' }
  | { kind: 'single'; range: ByteRange }
  | { kind: 'unsatisfiable' }
  | { kind: 'unsupported' };

const RANGE_HEADER_PATTERN = /^bytes=(\d*)-(\d*)$/;

/**
 * Parses a `Range` header value against a known resource size.
 *
 * - Missing/absent header -> { kind: 'none' }
 * - Multiple ranges, wrong unit, or malformed syntax -> { kind: 'unsupported' }
 * - Range outside [0, size-1], or start > end -> { kind: 'unsatisfiable' }
 * - Otherwise -> { kind: 'single', range: { start, end } }, clamped to size-1.
 */
export function parseRangeHeader(header: string | undefined, size: number): RangeParseResult {
  if (!header) return { kind: 'none' };
  if (size <= 0) return { kind: 'unsatisfiable' };

  if (header.includes(',')) return { kind: 'unsupported' };

  const match = RANGE_HEADER_PATTERN.exec(header.trim());
  if (!match) return { kind: 'unsupported' };

  const [, startText, endText] = match;
  if (startText === '' && endText === '') return { kind: 'unsupported' };

  let start: number;
  let end: number;

  if (startText === '') {
    // Suffix range: "bytes=-N" means the last N bytes.
    const suffixLength = Number.parseInt(endText ?? '', 10);
    if (!Number.isFinite(suffixLength) || suffixLength <= 0) {
      return { kind: 'unsatisfiable' };
    }
    start = Math.max(0, size - suffixLength);
    end = size - 1;
  } else {
    start = Number.parseInt(startText ?? '', 10);
    if (!Number.isFinite(start) || start < 0) return { kind: 'unsatisfiable' };
    if (endText === '') {
      end = size - 1;
    } else {
      end = Number.parseInt(endText ?? '', 10);
      if (!Number.isFinite(end)) return { kind: 'unsatisfiable' };
    }
  }

  if (start > end) return { kind: 'unsatisfiable' };
  if (start >= size) return { kind: 'unsatisfiable' };

  end = Math.min(end, size - 1);

  return { kind: 'single', range: { start, end } };
}
