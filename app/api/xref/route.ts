import { NextRequest, NextResponse } from 'next/server';
import { xref } from '@/lib/siliconexpert';
import { findByPN } from '@/lib/excel';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const BASE_LABELS: Record<string, string> = {
  A: 'Exact',
  B: 'Similar',
  C: 'Functional',
  D: 'Different',
  E: 'Enhanced',
  F: 'Footprint',
  G: 'Direct',
};

function labelFor(code: string) {
  if (!code) return '';
  const [head, tail] = code.split('/');
  const h = BASE_LABELS[head] ?? head;
  return tail ? `${h} · ${tail[0].toUpperCase()}${tail.slice(1).toLowerCase()}` : h;
}

const f = (v: unknown): number | null => {
  const s = String(v ?? '').trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};

export async function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  let comId = (p.get('comId') ?? '').trim();
  const pn = (p.get('pn') ?? '').trim();
  let mpn = (p.get('mpn') ?? '').trim();
  let manufacturer = (p.get('manufacturer') ?? '').trim();
  if (!comId && !pn && !mpn) {
    return NextResponse.json({ error: 'missing comId, pn, or mpn' }, { status: 400 });
  }

  if (pn && !comId) {
    const rows = findByPN(pn);
    if (rows.length) {
      if (rows[0].comId) comId = rows[0].comId;
      else if (!mpn) {
        mpn = rows[0].mpn;
        manufacturer = manufacturer || rows[0].manufacturer;
      }
    }
  }

  const key: { partNumber?: string; comId?: string; manufacturer?: string } = {};
  if (comId) key.comId = comId;
  else if (mpn) {
    key.partNumber = mpn;
    if (manufacturer) key.manufacturer = manufacturer;
  } else if (pn) {
    key.partNumber = pn;
  }

  const raw = (await xref([key])) as {
    Result?: { CrossData?: Record<string, unknown> };
  };
  const cd = (raw?.Result?.CrossData ?? {}) as Record<string, unknown>;
  let dto = cd.CrossDto;
  if (dto && !Array.isArray(dto)) dto = [dto];
  const list = (Array.isArray(dto) ? dto : []) as Array<Record<string, unknown>>;

  const seen = new Set<string>();
  const crosses = list
    .map((r) => {
      const cid = String(r.CrossID ?? '').trim();
      if (cid && seen.has(cid)) return null;
      if (cid) seen.add(cid);
      const pricing = (r.CrossPricingData ?? {}) as Record<string, unknown>;
      const tCode = String(r.Type ?? '').trim().toUpperCase();
      return {
        crossId: cid,
        partNumber: String(r.CrossPartNumber ?? ''),
        manufacturer: String(r.CrossManufacturer ?? ''),
        lifecycle: String(r.CrossLifecycle ?? ''),
        description: String(r.CrossDescription ?? ''),
        datasheet: String(r.CrossDatasheet ?? ''),
        rohs: String(r.CrossRoHSStatus ?? ''),
        packaging: String(r.CrossPackaging ?? ''),
        type: tCode,
        typeLabel: labelFor(tCode),
        comment: String(r.Comment ?? ''),
        formFitFunction: String(r.FormFitFunction ?? ''),
        replacementSource: String(r.ReplacementSource ?? ''),
        pricing: {
          min: f(pricing.MinimumPrice),
          avg: f(pricing.AveragePrice),
          minLeadtime: String(pricing.MinLeadtime ?? ''),
          maxLeadtime: String(pricing.Maxleadtime ?? ''),
        },
      };
    })
    .filter((r): r is NonNullable<typeof r> => !!r);

  return NextResponse.json({
    status: crosses.length ? 'ok' : 'empty',
    reason: crosses.length
      ? ''
      : 'SiliconExpert /xref returned no cross references for this part',
    reqPartNumber: String(cd.ReqPartNumber ?? ''),
    reqManufacturer: String(cd.ReqManufacturer ?? ''),
    reqComId: String(cd.ReqComId ?? ''),
    count: Number(cd.CrossCount ?? crosses.length) || 0,
    crosses,
    query: key,
  });
}
