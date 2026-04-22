/**
 * File-backed recent-searches store for the Next.js runtime.
 *
 * Stdlib-only, no binary dependencies. Reads/writes a JSON file at
 * RECENT_DB_PATH (default ./recent_searches.json) with an advisory
 * per-process mutex around writes. Matches the shape served by the
 * Flask backend's /api/recent so the frontend code is identical.
 */
import fs from 'node:fs';
import path from 'node:path';

export type RecentEntry = {
  pn: string;
  mpn: string;
  manufacturer: string;
  comId: string;
  lifecycle: string;
  yeol: number | null;
  risk: number | null;
  source: string;
  kind: string;
  searchedAt: string;
};

const FILE = path.resolve(
  process.env.RECENT_DB_PATH?.replace(/\.db$/, '.json') ?? './recent_searches.json',
);

let writing: Promise<void> = Promise.resolve();

function readAll(): RecentEntry[] {
  try {
    const text = fs.readFileSync(FILE, 'utf8');
    const data = JSON.parse(text);
    return Array.isArray(data) ? (data as RecentEntry[]) : [];
  } catch {
    return [];
  }
}

function writeAll(rows: RecentEntry[]): void {
  try {
    fs.mkdirSync(path.dirname(FILE), { recursive: true });
    fs.writeFileSync(FILE, JSON.stringify(rows, null, 2));
  } catch {
    /* fail-soft — telemetry must never break a request */
  }
}

export async function record(entry: Partial<RecentEntry> & { pn: string }): Promise<void> {
  const pn = (entry.pn || '').trim();
  if (!pn) return;
  await (writing = writing.then(() => {
    const rows = readAll();
    rows.push({
      pn,
      mpn: entry.mpn ?? '',
      manufacturer: entry.manufacturer ?? '',
      comId: entry.comId ?? '',
      lifecycle: entry.lifecycle ?? '',
      yeol: entry.yeol ?? null,
      risk: entry.risk ?? null,
      source: entry.source ?? '',
      kind: entry.kind ?? 'single',
      searchedAt: new Date().toISOString(),
    });
    // Cap storage at a sensible number so the file stays small.
    if (rows.length > 5000) rows.splice(0, rows.length - 5000);
    writeAll(rows);
  }).catch(() => undefined));
}

export function listRecent(limit = 10): RecentEntry[] {
  const rows = readAll();
  // Dedupe by PN, keep most recent per PN, newest first.
  const byPN = new Map<string, RecentEntry>();
  for (const r of rows) {
    const prev = byPN.get(r.pn);
    if (!prev || r.searchedAt > prev.searchedAt) byPN.set(r.pn, r);
  }
  return [...byPN.values()]
    .sort((a, b) => (a.searchedAt < b.searchedAt ? 1 : -1))
    .slice(0, limit);
}

export async function clear(): Promise<number> {
  const n = readAll().length;
  writeAll([]);
  return n;
}
