import { NextRequest, NextResponse } from 'next/server';
import { manufacturers } from '@/lib/siliconexpert';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get('q')?.trim() ?? '';
  if (!q) return NextResponse.json({ resultSize: '0', Result: { MfrDto: [] } });
  return NextResponse.json(await manufacturers(q));
}
