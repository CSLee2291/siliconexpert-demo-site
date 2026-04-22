import { NextRequest, NextResponse } from 'next/server';
import { search } from '@/lib/service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get('q') ?? '';
  const result = await search(q);
  return NextResponse.json(result);
}
