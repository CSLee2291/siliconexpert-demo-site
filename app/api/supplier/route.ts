import { NextRequest, NextResponse } from 'next/server';
import { supplierProfile } from '@/lib/siliconexpert';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const name = req.nextUrl.searchParams.get('name')?.trim() ?? '';
  if (!name) return NextResponse.json({ error: 'missing name' }, { status: 400 });
  return NextResponse.json(await supplierProfile(name));
}
