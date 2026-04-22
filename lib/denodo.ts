import { Agent, fetch as undiciFetch } from 'undici';

const insecureAgent = new Agent({ connect: { rejectUnauthorized: false } });

function useDev() {
  return String(process.env.DENODO_REST_USE_DEV_API ?? 'false').toLowerCase() === 'true';
}

function base() {
  return (useDev()
    ? process.env.DENODO_REST_DEV_BASE_URL
    : process.env.DENODO_REST_BASE_URL
  )?.replace(/\/$/, '') ?? '';
}

function auth() {
  const user = useDev()
    ? process.env.DENODO_REST_DEV_USERNAME
    : process.env.DENODO_REST_USERNAME;
  const pw = useDev()
    ? process.env.DENODO_REST_DEV_PASSWORD
    : process.env.DENODO_REST_PASSWORD;
  return `Basic ${Buffer.from(`${user ?? ''}:${pw ?? ''}`).toString('base64')}`;
}

const VIEW = 'iv_plm_allparts_latest';

function extractRows(j: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(j)) return j.filter((r): r is Record<string, unknown> => typeof r === 'object');
  if (j && typeof j === 'object') {
    for (const k of ['elements', 'value', 'rows', 'result'] as const) {
      const v = (j as Record<string, unknown>)[k];
      if (Array.isArray(v)) return v.filter((r): r is Record<string, unknown> => typeof r === 'object');
    }
  }
  return [];
}

export function isConfigured(): boolean {
  if (!base()) return false;
  const useDev = String(process.env.DENODO_REST_USE_DEV_API ?? 'false').toLowerCase() === 'true';
  const user = useDev
    ? process.env.DENODO_REST_DEV_USERNAME
    : process.env.DENODO_REST_USERNAME;
  return Boolean(user);
}

export async function findItemEx(
  itemNumber: string,
): Promise<{ row: Record<string, unknown> | null; error: string | null }> {
  const b = base();
  if (!b) return { row: null, error: null };
  const safe = itemNumber.replace(/'/g, "''");
  const headers = { Authorization: auth(), Accept: 'application/json' };

  const candidates = [
    `${b}/views/${VIEW}?$filter=${encodeURIComponent(`Item_Number = '${safe}'`)}&$format=JSON`,
    `${b}/${VIEW}?$filter=${encodeURIComponent(`Item_Number = '${safe}'`)}&$format=JSON`,
  ];

  const connectionErrors: string[] = [];
  let httpError: string | null = null;

  for (const url of candidates) {
    try {
      const res = await undiciFetch(url, {
        headers,
        // @ts-expect-error undici typing quirk
        dispatcher: insecureAgent,
      });
      if (res.status === 404) continue;
      if (res.status === 401 || res.status === 403) {
        return { row: null, error: `Denodo auth failed (${res.status})` };
      }
      if (!res.ok) {
        httpError = `Denodo HTTP ${res.status}`;
        continue;
      }
      const j = await res.json();
      const rows = extractRows(j);
      if (rows.length) return { row: rows[0], error: null };
      return { row: null, error: null };
    } catch (err) {
      connectionErrors.push(String(err));
      continue;
    }
  }
  if (connectionErrors.length) {
    return { row: null, error: 'Denodo unreachable · ' + connectionErrors[0] };
  }
  if (httpError) return { row: null, error: httpError };
  return { row: null, error: null };
}

// Back-compat: swallow the error.
export async function findItem(itemNumber: string): Promise<Record<string, unknown> | null> {
  const { row } = await findItemEx(itemNumber);
  return row;
}
