import { NextRequest, NextResponse } from 'next/server';
import { pcn } from '@/lib/siliconexpert';
import { findByPN } from '@/lib/excel';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  let comId = (p.get('comId') ?? '').trim();
  const pn = (p.get('pn') ?? '').trim();
  if (pn && !comId) {
    const rows = findByPN(pn);
    if (rows.length && rows[0].comId) comId = rows[0].comId;
  }
  if (!comId && !pn) {
    return NextResponse.json({ error: 'missing comId or pn' }, { status: 400 });
  }
  const raw = (await pcn(comId ? { comId } : { partNumber: pn })) as {
    Result?: { PCNData?: Record<string, unknown> };
  };
  const data = (raw?.Result?.PCNData ?? {}) as Record<string, unknown>;
  let dto = data.PCNDto;
  if (dto && !Array.isArray(dto)) dto = [dto];
  const list = (Array.isArray(dto) ? dto : []) as Array<Record<string, unknown>>;
  const pcns = list.map(r => ({
    pcnNumber: String(r.PCNNumber ?? ''),
    manufacturer: String(r.Manufacturer ?? ''),
    typeOfChange: String(r.TypeOfChange ?? ''),
    description: String(r.DescriptionOfChange ?? ''),
    source: String(r.Source ?? r.PcnSource ?? ''),
    notificationDate: String(r.NotificationDate ?? ''),
    effectiveDate: String(r.EffectiveDate ?? ''),
    lastTimeBuyDate: String(r.LastTimeBuyDate ?? ''),
    lastShipDate: String(r.LastShipDate ?? ''),
    affectedProduct: String(r.AffectedProductName ?? ''),
    pcnId: String(r.PCNId ?? ''),
  }));
  return NextResponse.json({
    status: pcns.length ? 'ok' : 'empty',
    reason: pcns.length ? '' : 'No PCNs returned by SE /pcn for this part',
    reqComId: String(data.ReqComId ?? comId),
    reqPartNumber: String(data.ReqPartNumber ?? pn),
    count: pcns.length,
    pcns,
  });
}
