/**
 * The rules of Mirror Home's board, ported from BoardItems.java, BoardApi.java
 * and BoardTime.java so that the fake mirror accepts and refuses what the
 * real one does, in the same words.
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const WRITABLE = ["kind", "title", "body", "due", "done", "priority", "expiresAt", "ttlSeconds", "source"];
const READ_ONLY = ["id", "createdAt", "updatedAt", "doneAt", "state", "showing", "dueIso", "expiresAtIso"];
const GUIDE = "/api/v1/board/guide";
const EXAMPLE = "2026-10-03T09:00:00-07:00";
const ISO = /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?\s?([Zz]|[+-]\d{2}(?::?\d{2})?)?$/;

export class BoardRefusal extends Error {
  constructor(status, field, message) {
    super(message);
    this.status = status;
    this.field = field;
  }

  body() {
    const body = { error: this.message };
    if (this.field) body.field = this.field;
    body.guide = GUIDE;
    return body;
  }
}

const invalid = (field, message) => new BoardRefusal(400, field, message);

function joined(values) {
  return values.map((value, index) => (index === 0 ? "" : index === values.length - 1 ? " or " : ", ") + value).join("");
}

function iso(ms) {
  return new Date(ms).toISOString().slice(0, 19) + "Z";
}

/** BoardTime.parse. */
function parseTime(value, field) {
  let ms;
  if (typeof value === "number") {
    if (!Number.isInteger(value)) throw invalid(field, `${field} must be a whole number of milliseconds`);
    if (value > 0 && value < 100_000_000_000) {
      throw invalid(field, `${field} looks like seconds. Give milliseconds since 1970, or an ISO 8601 time such as ${EXAMPLE}`);
    }
    ms = value;
  } else if (typeof value === "string") {
    const match = ISO.exec(value.trim());
    if (!match) {
      throw invalid(field, `${field} is not a time the Mirror can read. Use milliseconds since 1970, or ISO 8601 with an offset such as ${EXAMPLE}`);
    }
    if (!match[8]) {
      throw invalid(field, `${field} needs its offset from UTC, for example ${EXAMPLE} or 2026-10-03T16:00:00Z`);
    }
    const [year, month, day, hour, minute] = match.slice(1, 6).map(Number);
    const second = match[6] ? Number(match[6]) : 0;
    const utc = Date.UTC(year, month - 1, day, hour, minute, second);
    const check = new Date(utc);
    if (check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day || hour > 23 || minute > 59 || second > 59) {
      throw invalid(field, `${field} is not a real date and time: ${value}`);
    }
    let offset = 0;
    if (match[8].length > 1) {
      const digits = match[8].slice(1).replace(":", "");
      offset = (Number(digits.slice(0, 2)) * 60 + (digits.length > 2 ? Number(digits.slice(2)) : 0)) * (match[8][0] === "-" ? -1 : 1);
    }
    ms = utc - offset * 60_000;
  } else {
    throw invalid(field, `${field} must be a time: milliseconds since 1970, or ISO 8601 with an offset such as ${EXAMPLE}`);
  }
  if (ms < 1577836800000 || ms >= 4102444800000) throw invalid(field, `${field} must fall between 2020 and 2100`);
  return ms;
}

/** BoardItems: what the board holds and the rules for writing to it. */
export class Board {
  /** @param {() => number} now */
  constructor(now) {
    this.now = now;
    this.items = [];
    this.version = 0;
    this.nextId = 1;
  }

  expire() {
    const now = this.now();
    const kept = this.items.filter((item) => item.expiresAt === undefined || item.expiresAt > now);
    if (kept.length !== this.items.length) {
      this.items = kept;
      this.version += 1;
    }
  }

  build(id, existing, request, merge, defaultSource) {
    const now = this.now();
    for (const key of Object.keys(request)) {
      if (!WRITABLE.includes(key) && !READ_ONLY.includes(key)) {
        throw invalid(key, `Unknown field "${key}". An item takes ${joined(WRITABLE)}`);
      }
    }
    const draft = {
      kind: "note", title: undefined, body: "", priority: "normal", source: defaultSource || "unknown",
      due: undefined, doneAt: undefined, expiresAt: undefined, done: false, autoExpiry: true,
      createdAt: existing ? existing.createdAt : now,
    };
    const wasDone = Boolean(existing?.done);
    if (wasDone) draft.doneAt = existing.doneAt;
    if (merge) {
      Object.assign(draft, {
        kind: existing.kind, title: existing.title, body: existing.body, priority: existing.priority,
        source: existing.source, due: existing.due, done: wasDone, autoExpiry: existing.autoExpiry, expiresAt: existing.expiresAt,
      });
    }
    if ("kind" in request) {
      if (!["note", "todo", "reminder"].includes(request.kind)) throw invalid("kind", "kind must be one of note, todo or reminder");
      draft.kind = request.kind;
    }
    if ("title" in request) {
      if (typeof request.title !== "string") throw invalid("title", "title must be text");
      const title = request.title.trim();
      if (!title) throw invalid("title", "title cannot be empty");
      if (title.length > 120) throw invalid("title", "title holds up to 120 characters; put the rest in body");
      if (/[\u0000-\u001f\u007f-\u009f]/.test(title)) throw invalid("title", "title is one line; put further lines in body");
      draft.title = title;
    } else if (!merge) {
      throw invalid("title", "title is required: the line the Mirror shows");
    }
    if ("body" in request) {
      if (request.body !== null && typeof request.body !== "string") throw invalid("body", "body must be text");
      const body = (request.body ?? "").replace(/\r\n?/g, "\n").trim();
      if (body.length > 500) throw invalid("body", "body holds up to 500 characters");
      draft.body = body;
    }
    if ("priority" in request) {
      if (!["low", "normal", "high"].includes(request.priority)) throw invalid("priority", "priority must be one of low, normal or high");
      draft.priority = request.priority;
    }
    if (request.source !== undefined && request.source !== null) {
      if (typeof request.source !== "string" || !request.source.trim() || request.source.trim().length > 40) {
        throw invalid("source", "source is a short name for who posted this, up to 40 characters");
      }
      draft.source = request.source.trim();
    }
    if ("due" in request) draft.due = request.due === null ? undefined : parseTime(request.due, "due");
    if ("done" in request) {
      if (typeof request.done !== "boolean") throw invalid("done", "done must be true or false");
      draft.done = request.done;
    }
    const hasTtl = request.ttlSeconds !== undefined && request.ttlSeconds !== null;
    if ("expiresAt" in request && hasTtl) {
      throw invalid("ttlSeconds", "Give expiresAt or ttlSeconds, not both: they say the same thing");
    }
    if (hasTtl) {
      const ttl = request.ttlSeconds;
      if (!Number.isInteger(ttl) || ttl < 1 || ttl > 366 * 24 * 60 * 60) {
        throw invalid("ttlSeconds", "ttlSeconds must be a whole number of seconds from 1 to 31622400");
      }
      draft.expiresAt = now + ttl * 1000;
      draft.autoExpiry = false;
    } else if ("expiresAt" in request) {
      if (request.expiresAt === null || request.expiresAt === "never") {
        draft.expiresAt = undefined;
      } else {
        const at = parseTime(request.expiresAt, "expiresAt");
        if (at <= now) throw invalid("expiresAt", `expiresAt is already past; the Mirror's clock reads ${iso(now)}`);
        draft.expiresAt = at;
      }
      draft.autoExpiry = false;
    }
    if (draft.kind === "note") {
      if (draft.due !== undefined) {
        throw invalid("due", "A note has no due time. Make it a \"reminder\" or a \"todo\", or leave due out");
      }
      if (draft.done) throw invalid("done", "A note cannot be done. Make it a \"todo\", or delete the note");
    }
    if (draft.kind === "reminder" && draft.due === undefined) {
      throw invalid("due", `A reminder needs due, the moment it is about: milliseconds since 1970, or ISO 8601 with an offset such as ${EXAMPLE}`);
    }
    if (!draft.done) draft.doneAt = undefined;
    else if (draft.doneAt === undefined) draft.doneAt = now;
    if (draft.autoExpiry) draft.expiresAt = (draft.due === undefined ? now : Math.max(now, draft.due)) + DAY_MS;
    return { id, ...draft, updatedAt: now };
  }

  state(item) {
    const now = this.now();
    if (item.done) return "done";
    if (item.due === undefined) return "open";
    if (item.due <= now) return "overdue";
    return item.due - now <= 60 * 60 * 1000 ? "soon" : "open";
  }

  view(item) {
    const now = this.now();
    return {
      id: item.id,
      kind: item.kind,
      title: item.title,
      body: item.body,
      due: item.due ?? null,
      dueIso: item.due === undefined ? null : iso(item.due),
      done: item.done,
      doneAt: item.doneAt ?? null,
      priority: item.priority,
      expiresAt: item.expiresAt ?? null,
      expiresAtIso: item.expiresAt === undefined ? null : iso(item.expiresAt),
      source: item.source,
      createdAt: item.createdAt,
      updatedAt: item.updatedAt,
      state: this.state(item),
      showing: !item.done || now - (item.doneAt ?? now) < 10 * 60 * 1000,
    };
  }

  requireRoom() {
    if (this.items.length >= 100) {
      throw new BoardRefusal(409, null, "The board holds at most 100 items. Remove some first, for example DELETE /api/v1/board/items?done=true");
    }
  }

  create(request, source) {
    this.expire();
    if ("id" in request) throw invalid("id", "POST picks the id. To choose it yourself, PUT /api/v1/board/items/{id}");
    this.requireRoom();
    const item = this.build(`item${String(this.nextId++).padStart(4, "0")}`, null, request, false, source);
    this.items.push(item);
    this.version += 1;
    return this.view(item);
  }

  put(id, request, source) {
    this.expire();
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(id)) {
      throw invalid("id", "An id is 1 to 64 letters, digits, dots, dashes or underscores, and starts with a letter or digit");
    }
    if ("id" in request && request.id !== id) throw invalid("id", "The id in the body is not the id in the path");
    const index = this.items.findIndex((item) => item.id === id);
    if (index < 0) this.requireRoom();
    const item = this.build(id, index < 0 ? null : this.items[index], request, false, source);
    if (index < 0) this.items.push(item);
    else this.items[index] = item;
    this.version += 1;
    return { item: this.view(item), created: index < 0 };
  }

  patch(id, request) {
    this.expire();
    if ("id" in request && request.id !== id) throw invalid("id", "The id in the body is not the id in the path");
    const index = this.items.findIndex((item) => item.id === id);
    if (index < 0) return null;
    this.items[index] = this.build(id, this.items[index], request, true, null);
    this.version += 1;
    return this.view(this.items[index]);
  }

  find(id) {
    this.expire();
    const item = this.items.find((candidate) => candidate.id === id);
    return item ? this.view(item) : null;
  }

  delete(id) {
    this.expire();
    const index = this.items.findIndex((item) => item.id === id);
    if (index < 0) return false;
    this.items.splice(index, 1);
    this.version += 1;
    return true;
  }

  deleteMatching(filter) {
    this.expire();
    const kept = this.items.filter((item) => !matches(item, filter));
    const removed = this.items.length - kept.length;
    if (removed > 0) {
      this.items = kept;
      this.version += 1;
    }
    return removed;
  }

  /** Overdue first, then due soon, then the rest, then done; as the glass lists them. */
  list(filter = {}) {
    this.expire();
    const urgency = { overdue: 0, soon: 1, open: 2, done: 3 };
    const importance = { high: 0, normal: 1, low: 2 };
    return this.items
      .filter((item) => matches(item, filter))
      .sort(
        (left, right) =>
          urgency[this.state(left)] - urgency[this.state(right)] ||
          importance[left.priority] - importance[right.priority] ||
          (left.due ?? Infinity) - (right.due ?? Infinity) ||
          left.createdAt - right.createdAt ||
          left.id.localeCompare(right.id),
      )
      .map((item) => this.view(item));
  }
}

function matches(item, filter) {
  return (
    (filter.kind === undefined || filter.kind === item.kind) &&
    (filter.source === undefined || filter.source === item.source) &&
    (filter.done === undefined || filter.done === item.done)
  );
}

export function notFound(id) {
  return new BoardRefusal(
    404,
    "id",
    `The board has no item "${id}". It may have expired or been removed; GET /api/v1/board/items lists what is there`,
  );
}

export { invalid };
