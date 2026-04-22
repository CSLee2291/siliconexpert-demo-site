import { NextRequest, NextResponse } from 'next/server';
import { clear, listRecent } from '@/lib/recentStore';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const raw = Number(req.nextUrl.searchParams.get('limit') ?? '10');
  const limit = Math.max(1, Math.min(Number.isFinite(raw) ? raw : 10, 50));
  return NextResponse.json({ items: listRecent(limit) });
}

export async function DELETE() {
  const cleared = await clear();
  return NextResponse.json({ cleared });
}
