import { NextResponse } from 'next/server';
import { userStatus } from '@/lib/siliconexpert';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  const j = await userStatus();
  return NextResponse.json(j);
}
