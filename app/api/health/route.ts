import { NextResponse } from 'next/server';
import { stats } from '@/lib/excel';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  return NextResponse.json({ ok: true, mode: 'nextjs', excel: stats() });
}
