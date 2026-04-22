import fs from 'node:fs';
import path from 'node:path';
import * as XLSX from 'xlsx';

export type Row = {
  pn: string;
  mpn: string;
  manufacturer: string;
  status: string;
  comId: string;
};

type Cache = {
  byPN: Map<string, Row[]>;
  byMPN: Map<string, Row[]>;
  rows: Row[];
};

const G = globalThis as unknown as { __excelCache?: Cache };

const norm = (v: unknown) => String(v ?? '').trim();
const key = (v: unknown) => norm(v).toUpperCase();

export function load(): Cache {
  if (G.__excelCache) return G.__excelCache;

  const envPath = process.env.EXCEL_PATH;
  const rel = envPath || path.join('acl_pn_comID', 'V_SE_MPN_LIST20260128.xlsx');
  const xlsxPath = path.isAbsolute(rel) ? rel : path.resolve(process.cwd(), rel);
  if (!fs.existsSync(xlsxPath)) {
    throw new Error(`Excel file not found: ${xlsxPath}`);
  }

  const buf = fs.readFileSync(xlsxPath);
  const wb = XLSX.read(buf, { type: 'buffer', cellDates: false });
  const ws = wb.Sheets[wb.SheetNames[0]];
  const raw = XLSX.utils.sheet_to_json<Record<string, unknown>>(ws, { defval: null });

  const rows: Row[] = [];
  const byPN = new Map<string, Row[]>();
  const byMPN = new Map<string, Row[]>();

  for (const r of raw) {
    const row: Row = {
      pn: norm(r.PN),
      mpn: norm(r.MPN),
      manufacturer: norm(r.Manufacturer),
      status: norm(r.Status),
      comId: norm(r.SE_ComID),
    };
    if (!row.pn && !row.mpn) continue;
    rows.push(row);
    if (row.pn) {
      const k = key(row.pn);
      (byPN.get(k) ?? byPN.set(k, []).get(k)!).push(row);
    }
    if (row.mpn) {
      const k = key(row.mpn);
      (byMPN.get(k) ?? byMPN.set(k, []).get(k)!).push(row);
    }
  }

  const cache: Cache = { rows, byPN, byMPN };
  G.__excelCache = cache;
  return cache;
}

export function findByPN(pn: string): Row[] {
  return load().byPN.get(key(pn)) ?? [];
}

export function findByMPN(mpn: string): Row[] {
  return load().byMPN.get(key(mpn)) ?? [];
}

export function search(query: string): Row[] {
  const q = key(query);
  if (!q) return [];
  const hits = findByPN(q);
  return hits.length ? hits : findByMPN(q);
}

export function rows(): Row[] {
  return load().rows;
}

export function stats() {
  const c = load();
  return { rows: c.rows.length, distinctPN: c.byPN.size, distinctMPN: c.byMPN.size };
}
