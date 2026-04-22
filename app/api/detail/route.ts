import { NextRequest, NextResponse } from 'next/server';
import { detail } from '@/lib/service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const pn = req.nextUrl.searchParams.get('pn');
  const comId = req.nextUrl.searchParams.get('comId') ?? req.nextUrl.searchParams.get('comid');
  if (!pn && !comId) {
    return NextResponse.json({ error: 'missing pn or comId' }, { status: 400 });
  }
  const result = await detail({ pn, comId });
  return NextResponse.json(result);
}
