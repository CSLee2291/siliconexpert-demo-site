import { Agent, fetch as undiciFetch } from 'undici';

const insecureAgent = new Agent({ connect: { rejectUnauthorized: false } });

const base = () =>
  (process.env.SILICONEXPERT_API_BASE || 'https://api.siliconexpert.com/ProductAPI/search').replace(
    /\/$/,
    '',
  );

function creds() {
  const login = process.env.SILICONEXPERT_LOGIN;
  const apiKey = process.env.SILICONEXPERT_API_KEY;
  if (!login || !apiKey) {
    throw new Error('SILICONEXPERT_LOGIN / SILICONEXPERT_API_KEY not set');
  }
  return { login, apiKey };
}

type Jar = string[];

const jar = (): Jar => [];

async function post(url: string, cookies: Jar, body?: URLSearchParams) {
  const headers: Record<string, string> = {
    Accept: 'application/json',
  };
  if (body) headers['Content-Type'] = 'application/x-www-form-urlencoded';
  if (cookies.length) headers['Cookie'] = cookies.join('; ');

  const res = await undiciFetch(url, {
    method: 'POST',
    headers,
    body,
    // @ts-expect-error undici typing quirk
    dispatcher: insecureAgent,
  });

  const setCookie = res.headers.getSetCookie?.() ?? [];
  for (const sc of setCookie) {
    const first = sc.split(';')[0];
    if (first) cookies.push(first);
  }

  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text, status: res.status };
  }
}

async function authenticate(cookies: Jar) {
  const { login, apiKey } = creds();
  const qs = new URLSearchParams({ login, apiKey });
  const url = `${base()}/authenticateUser?${qs}`;
  const j = await post(url, cookies);
  const ok = String(j?.Status?.Success ?? '').toLowerCase() === 'true';
  return { ok, response: j };
}

function form(extra: Record<string, string>) {
  const { login, apiKey } = creds();
  return new URLSearchParams({ login, apiKey, fmt: 'json', ...extra });
}

export async function partSearch(partNumber: string, manufacturer?: string) {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  const body = form({ partNumber, ...(manufacturer ? { manufacturer } : {}) });
  return post(`${base()}/partsearch`, cookies, body);
}

export async function partDetail(comIds: string[], { lifecycle = true } = {}) {
  const out: Record<string, unknown>[] = [];
  for (let i = 0; i < comIds.length; i += 50) {
    const batch = comIds.slice(i, i + 50).filter(Boolean);
    if (!batch.length) continue;
    const cookies = jar();
    const auth = await authenticate(cookies);
    if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
    const body = form({
      comIds: batch.join(','),
      ...(lifecycle ? { getLifeCycleData: '1' } : {}),
    });
    const j = await post(`${base()}/partDetail`, cookies, body);
    const dto = (j as { Results?: { ResultDto?: unknown } })?.Results?.ResultDto;
    if (Array.isArray(dto)) {
      for (const d of dto) if (d && typeof d === 'object') out.push(d as Record<string, unknown>);
    } else if (dto && typeof dto === 'object') {
      out.push(dto as Record<string, unknown>);
    }
  }
  return { Results: { ResultDto: out } };
}

export async function getAllTaxonomy() {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  return post(`${base()}/parametric/getAllTaxonomy`, cookies, form({}));
}

export async function getPlFeatures(plName: string, page = 1) {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  return post(
    `${base()}/parametric/getPlFeatures`,
    cookies,
    form({ plName, pageNumber: String(page) }),
  );
}

export async function getSearchResult(plName: string, page = 1) {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  return post(
    `${base()}/parametric/getSearchResult`,
    cookies,
    form({ plName, pageNumber: String(page) }),
  );
}

export async function manufacturers(mfr: string) {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  return post(`${base()}/manufacturers`, cookies, form({ mfr }));
}

export async function pcn(opts: { comId?: string; partNumber?: string }) {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  const extra: Record<string, string> = {};
  if (opts.comId) extra.comIds = String(opts.comId);
  else if (opts.partNumber) extra.partNumber = opts.partNumber;
  else return { error: 'missing comId or partNumber' };
  return post(`${base()}/pcn`, cookies, form(extra));
}

export async function xref(
  parts: Array<{ partNumber?: string; comId?: string; manufacturer?: string }>,
) {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  return post(`${base()}/xref`, cookies, form({ parts: JSON.stringify(parts) }));
}

export async function supplierProfile(manufacturerName: string) {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  return post(`${base()}/supplierProfile`, cookies, form({ manufacturerName }));
}

export async function userStatus() {
  const cookies = jar();
  const auth = await authenticate(cookies);
  if (!auth.ok) return { error: 'auth_failed', auth: auth.response };
  return post(`${base()}/userStatus`, cookies, form({}));
}

export async function resolveComId(
  mpn: string,
  manufacturer?: string,
): Promise<string | null> {
  try {
    const j = await partSearch(mpn, manufacturer);
    const raw = (j as { Result?: unknown })?.Result;
    let results: Array<Record<string, unknown>>;
    if (Array.isArray(raw)) results = raw as Array<Record<string, unknown>>;
    else if (raw && typeof raw === 'object') results = [raw as Record<string, unknown>];
    else results = [];
    if (!results.length) return null;
    if (manufacturer) {
      const mfr = manufacturer.trim().toLowerCase();
      for (const row of results) {
        const rowMfr = String(row?.Manufacturer ?? '').trim().toLowerCase();
        if (rowMfr === mfr || rowMfr.startsWith(mfr) || mfr.startsWith(rowMfr)) {
          const cid = String(row?.ComID ?? '').trim();
          if (cid) return cid;
        }
      }
    }
    const cid = String(results[0]?.ComID ?? '').trim();
    return cid || null;
  } catch {
    return null;
  }
}
