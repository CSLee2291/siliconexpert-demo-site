import { NextRequest, NextResponse } from 'next/server';
import { getPlFeatures } from '@/lib/siliconexpert';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const plName = req.nextUrl.searchParams.get('plName')?.trim() ?? '';
  const page = Number(req.nextUrl.searchParams.get('page') ?? '1') || 1;
  if (!plName) return NextResponse.json({ error: 'missing plName' }, { status: 400 });
  return NextResponse.json(await getPlFeatures(plName, page));
}
