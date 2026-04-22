import { NextResponse } from 'next/server';
import { getAllTaxonomy } from '@/lib/siliconexpert';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

let cached: unknown = null;

export async function GET() {
  if (!cached) cached = await getAllTaxonomy();
  return NextResponse.json(cached);
}
