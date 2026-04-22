import { NextRequest, NextResponse } from 'next/server';
import { bulkSearch } from '@/lib/service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

async function readPns(req: NextRequest): Promise<string> {
  if (req.method === 'POST') {
    const ct = req.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      const body = (await req.json().catch(() => ({}))) as { pns?: unknown };
      if (Array.isArray(body.pns)) return body.pns.map(String).join('\n');
      if (typeof body.pns === 'string') return body.pns;
    } else if (ct.includes('application/x-www-form-urlencoded')) {
      const form = await req.formData();
      const v = form.get('pns');
      if (typeof v === 'string') return v;
    }
  }
  return req.nextUrl.searchParams.get('pns') ?? '';
}

export async function GET(req: NextRequest) {
  const raw = await readPns(req);
  if (!raw.trim()) return NextResponse.json({ error: 'missing pns' }, { status: 400 });
  return NextResponse.json(await bulkSearch(raw));
}

export const POST = GET;
