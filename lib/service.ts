import * as excel from './excel';
import * as se from './siliconexpert';
import * as denodo from './denodo';
import * as recent from './recentStore';

export type Candidate = {
  pn: string;
  mpn: string;
  manufacturer: string;
  comId: string;
  status: string;
  source: string;
  denodo?: Record<string, unknown>;
};

function candidateFromRow(r: excel.Row, source: string): Candidate {
  return {
    pn: r.pn,
    mpn: r.mpn,
    manufacturer: r.manufacturer,
    comId: r.comId,
    status: r.status,
    source,
  };
}

export const BULK_LIMIT = 50;

function parseBulkList(raw: string): string[] {
  if (!raw) return [];
  const normalized = raw.replace(/[,;\t\r]+/g, '\n');
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of normalized.split('\n')) {
    const pn = line.trim();
    if (!pn || seen.has(pn.toLowerCase())) continue;
    seen.add(pn.toLowerCase());
    out.push(pn);
  }
  return out;
}

export async function bulkSearch(raw: string) {
  const pns = parseBulkList(raw);
  const total = pns.length;
  const truncated = total > BULK_LIMIT;
  const pnsCapped = pns.slice(0, BULK_LIMIT);

  const hits: Array<Candidate & { requested: string; reason?: string }> = [];
  const missing: string[] = [];
  const denodoConfigured = denodo.isConfigured();
  const denodoErrors = new Set<string>();

  for (const pn of pnsCapped) {
    const rows = excel.search(pn);
    if (rows.length) {
      const cand: Candidate & { requested: string; reason?: string } = {
        ...candidateFromRow(rows[0], 'excel'),
        requested: pn,
        reason: '',
      };
      if (!cand.comId && cand.mpn) {
        const resolved = await se.resolveComId(cand.mpn, cand.manufacturer || undefined);
        if (resolved) {
          cand.comId = resolved;
          cand.source = 'excel+partsearch';
        } else {
          cand.reason = 'Excel row had no SE_ComID and /partsearch found no match';
        }
      } else if (!cand.comId) {
        cand.reason = 'Excel row has no SE_ComID and no MPN';
      }
      recent.record({
        pn: cand.pn,
        mpn: cand.mpn,
        manufacturer: cand.manufacturer,
        comId: cand.comId,
        source: cand.source,
        kind: 'bulk',
      });
      hits.push(cand);
      continue;
    }

    let row: Record<string, unknown> | null = null;
    if (denodoConfigured) {
      try {
        const r = await denodo.findItemEx(pn);
        row = r.row;
        if (r.error) denodoErrors.add(r.error);
      } catch (err) {
        denodoErrors.add(`Denodo lookup failed: ${String(err)}`);
      }
    }
    if (row) {
      hits.push({
        pn: String(row.Item_Number ?? pn),
        mpn: '',
        manufacturer: String(row.Manufacturer ?? ''),
        comId: '',
        status: String(row.Status ?? ''),
        source: 'denodo',
        requested: pn,
        reason: 'Found in Denodo but not mapped to a SE ComID',
      });
      continue;
    }
    missing.push(pn);
  }

  return {
    query: raw,
    total,
    limit: BULK_LIMIT,
    truncated,
    missing,
    hits,
    source: 'bulk' as const,
    denodo: {
      configured: denodoConfigured,
      online: denodoConfigured && denodoErrors.size === 0,
      error: denodoErrors.size ? [...denodoErrors].sort().join('; ') : null,
    },
  };
}

export async function search(query: string) {
  const q = query.trim();
  if (!q) return { query: q, hits: [], source: 'empty', reason: '' };

  const rows = excel.search(q);
  if (rows.length) {
    const hits: Array<Candidate & { reason?: string }> = [];
    for (const r of rows) {
      const cand: Candidate & { reason?: string } = {
        ...candidateFromRow(r, 'excel'),
        reason: '',
      };
      if (!cand.comId && cand.mpn) {
        const resolved = await se.resolveComId(cand.mpn, cand.manufacturer || undefined);
        if (resolved) {
          cand.comId = resolved;
          cand.source = 'excel+partsearch';
        } else {
          cand.reason =
            `Excel has no SE_ComID and /partsearch found no match for MPN '${cand.mpn}'` +
            (cand.manufacturer ? ` · ${cand.manufacturer}` : '');
        }
      } else if (!cand.comId) {
        cand.reason = 'Excel has no SE_ComID and no MPN to resolve from';
      }
      recent.record({
        pn: cand.pn,
        mpn: cand.mpn,
        manufacturer: cand.manufacturer,
        comId: cand.comId,
        source: cand.source,
        kind: 'single',
      });
      hits.push(cand);
    }
    return { query: q, hits, source: 'excel' };
  }

  const denodoConfigured = denodo.isConfigured();
  let row: Record<string, unknown> | null = null;
  let denodoError: string | null = null;
  if (denodoConfigured) {
    try {
      const r = await denodo.findItemEx(q);
      row = r.row;
      denodoError = r.error;
    } catch (err) {
      denodoError = `Denodo lookup failed: ${String(err)}`;
    }
  }
  const denodoStatus = {
    configured: denodoConfigured,
    online: denodoConfigured && !denodoError,
    error: denodoError,
  };

  if (row) {
    const cand: Candidate & { reason?: string } = {
      pn: String((row.Item_Number ?? row.item_number ?? q) as string | number),
      mpn: '',
      manufacturer: String((row.Manufacturer ?? row.manufacturer ?? '') as string),
      comId: '',
      status: String((row.Status ?? row.status ?? '') as string),
      source: 'denodo',
      denodo: row,
      reason:
        'Part found in Denodo but not in local SE mapping · no ComID available',
    };
    return {
      query: q,
      hits: [cand],
      source: 'denodo',
      denodo: denodoStatus,
    };
  }

  const reason = denodoError
    ? `'${q}' not found in Excel mapping · Denodo fallback unavailable (${denodoError}) · SiliconExpert API still reachable for known parts`
    : !denodoConfigured
      ? `'${q}' not found in Excel mapping · Denodo fallback not configured`
      : `'${q}' not found in Excel mapping and not in Denodo iv_plm_allparts_latest`;

  return {
    query: q,
    hits: [],
    source: 'none',
    reason,
    denodo: denodoStatus,
  };
}

function findCandidate(pn?: string | null, comId?: string | null): Candidate | null {
  if (pn) {
    const r = excel.findByPN(pn);
    if (r.length) return candidateFromRow(r[0], 'excel');
  }
  if (comId) {
    for (const r of excel.rows()) {
      if (r.comId === comId) return candidateFromRow(r, 'excel');
    }
    return { pn: '', mpn: '', manufacturer: '', comId, status: '', source: 'comid' };
  }
  return null;
}

const toFloat = (v: unknown): number | null => {
  const s = String(v ?? '').trim().replace('%', '');
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};

const intFromCount = (v: unknown): number | null => {
  const s = String(v ?? '').trim();
  if (!s) return null;
  const m = s.match(/\d+/);
  return m ? Number(m[0]) : null;
};

const weeksFrom = (v: unknown): number | null => {
  const s = String(v ?? '').trim().replace(/Week\(s\)/i, '').trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};

function asArray<T = Record<string, unknown>>(v: unknown): T[] {
  if (Array.isArray(v)) return v as T[];
  if (v && typeof v === 'object') return [v as T];
  return [];
}

function normalizeCommercial(dto: Record<string, unknown>) {
  const rf = (dto.ResilienceRatingFactors ?? {}) as Record<string, unknown>;
  const rd = (dto.ResilienceRatingdetails ?? {}) as Record<string, unknown>;
  const cf = (dto.FullCounterfeitData ?? {}) as Record<string, unknown>;
  const summary = (dto.SummaryData ?? {}) as Record<string, unknown>;
  const aos = (rd.AssuranceOfSupply ?? {}) as Record<string, unknown>;
  const multi = (aos.multiSourcingRiskDto ?? {}) as Record<string, unknown>;
  const inv = (aos.inventoryRiskDto ?? {}) as Record<string, unknown>;

  // Pricing + lead time
  const pricing = (dto.PricingData ?? {}) as Record<string, unknown>;

  // Price breaks
  const breaks = asArray<Record<string, unknown>>(
    (dto.PriceBreaksData as Record<string, unknown> | undefined)?.PriceBreaksDto,
  )
    .map(b => ({
      qty: intFromCount(b.PriceBreaK),
      avg: toFloat(b.AveragePrice),
      min: toFloat(b.MinPrice),
    }))
    .sort((a, b) => (a.qty ?? Infinity) - (b.qty ?? Infinity));

  // Price history
  const history = asArray<Record<string, unknown>>(dto.PriceAndLeadTimeHistory)
    .map(h => ({
      date: String(h.LastUpdatedate ?? ''),
      min: toFloat(h.MinimumPrice),
      avg: toFloat(h.AveragePrice),
      minLead: weeksFrom(h.MinLeadtime),
      maxLead: weeksFrom(h.Maxleadtime),
    }))
    .reverse();

  // Inventory
  const totalInventory = intFromCount(dto.TotalInventory);
  const averageInventory = intFromCount(dto.AverageInventory);

  // Distributors
  const distributors = asArray<Record<string, unknown>>(
    (dto.FranchisedInventoryData as Record<string, unknown> | undefined)?.FranchisedInventoryDto,
  )
    .map(d => ({
      distributor: String(d.Distributor ?? ''),
      quantity: intFromCount(d.Quantity),
      buyNowLink: String(d.BuyNowLink ?? ''),
      lastUpdated: String(d.LastUpdated ?? ''),
    }))
    .sort((a, b) => (b.quantity ?? 0) - (a.quantity ?? 0));

  const authorizedDistributorsCount =
    intFromCount(summary.AuthorizedDistributors) ??
    intFromCount(cf.AuthorizedDistributorswithStockCount);
  const distributorsWithStock = distributors.filter(d => (d.quantity ?? 0) > 0).length;

  return {
    resilienceRating: toFloat(rf.ResilienceRating),
    resilienceGrade: String(rf.ResilienceRatingGrade ?? ''),
    otherSources: intFromCount(multi.countOfOtherSources),
    crossesAvailable: String(multi.crosseAavailableWithinPartCategory ?? ''),
    authorizedDistributorsCount,
    distributorsWithStock,
    counterfeitRisk: String(cf.CounterfeitOverallRisk ?? ''),
    counterfeitGrade: String(cf.OverallCounterfeitRiskGrade ?? ''),
    yearsSinceIntro: intFromCount(cf.TimeSinceMarketIntroduction),
    inventoryRiskGrade: String(inv.grade ?? ''),
    historicalShortages: String(cf.HistoricalShortagesInventoryReported ?? ''),
    priceMin: toFloat(pricing.MinimumPrice),
    priceAvg: toFloat(pricing.AveragePrice),
    minLeadWeeks: weeksFrom(pricing.MinLeadtime),
    maxLeadWeeks: weeksFrom(pricing.Maxleadtime),
    priceLastUpdated: String(pricing.LastUpdatedate ?? ''),
    priceBreaks: breaks,
    priceHistory: history,
    totalInventory,
    averageInventory,
    distributors,
  };
}

function normalizeParametric(dto: Record<string, unknown>): Array<{ name: string; value: string; unit: string }> {
  const pd = (dto.ParametricData ?? {}) as Record<string, unknown>;
  const feats = pd.Features;
  if (!Array.isArray(feats)) return [];
  return feats.filter((f): f is Record<string, unknown> => !!f && typeof f === 'object').map(f => ({
    name: String(f.FeatureName ?? ''),
    value: String(f.FeatureValue ?? ''),
    unit: String(f.FeatureUnit ?? ''),
  }));
}

function normalizePackaging(dto: Record<string, unknown>) {
  const pkg = (dto.PackagingData ?? {}) as Record<string, unknown>;
  const cas = (dto.PackageData ?? {}) as Record<string, unknown>;
  const s = (src: Record<string, unknown>, k: string) => String(src[k] ?? '');
  return {
    comId: String(dto.RequestedComID ?? ''),
    packagingSuffix: s(pkg, 'PackagingSuffix'),
    packaging: s(pkg, 'Packaging'),
    quantityOfPackaging: s(pkg, 'QuantityOfPackaging'),
    reelDiameter: s(pkg, 'ReelDiameter'),
    reelWidth: s(pkg, 'ReelWidth'),
    tapePitch: s(pkg, 'TapePitch'),
    tapeWidth: s(pkg, 'TapeWidth'),
    feedHolePitch: s(pkg, 'FeedHolePitch'),
    holeCenterToComponentCenter: s(pkg, 'HoleCenterToComponentCenter'),
    leadClinchHeight: s(pkg, 'LeadClinchHeight'),
    componentOrientation: s(pkg, 'ComponentOrientation'),
    packagingDocument: s(pkg, 'PackagingDocument'),
    tapeMaterial: s(pkg, 'TapeMaterial'),
    tapeType: s(pkg, 'TapeType'),
    supplierPackage: s(cas, 'SupplierPackage'),
    pinCount: s(cas, 'PinCount'),
    pcb: s(cas, 'PCB'),
    tab: s(cas, 'Tab'),
    packageDiameter: s(cas, 'PackageDiameter'),
    mounting: s(cas, 'Mounting'),
    packageLength: s(cas, 'PackageLength'),
    packageWidth: s(cas, 'PackageWidth'),
    packageHeight: s(cas, 'PackageHeight'),
    packageDescription: s(cas, 'PackageDescription'),
    packageMaterial: s(cas, 'PackageMaterial'),
    standardPackageName: s(cas, 'StandardPackageName'),
    seatedPlaneHeight: s(cas, 'SeatedPlaneHeight'),
    pinPitch: s(cas, 'PinPitch'),
    jedec: s(cas, 'Jedec'),
    packageOutline: s(cas, 'PackageOutline'),
    packageCase: s(cas, 'PackageCase'),
    leadShape: s(cas, 'LeadShape'),
    basicPackageType: s(cas, 'BasicPackageType'),
    packageWeight: s(cas, 'PackageWeight'),
    minimumSeatedPlaneHeight: s(cas, 'MinimumSeatedPlaneHeight'),
    packageOrientation: s(cas, 'PackageOrientation'),
  };
}

function normalizeCountries(dto: Record<string, unknown>) {
  const summary = (dto.SummaryData ?? {}) as Record<string, unknown>;
  const container = (summary.CountriesOfOrigin ?? {}) as Record<string, unknown>;
  const list = asArray<Record<string, unknown>>(container.CountryOfOrigin);
  const comId = String(dto.RequestedComID ?? '');
  return list.map(r => ({
    comId,
    country: String(r.Country ?? ''),
    source: String(r.Source ?? ''),
  }));
}

function normalizeDocuments(dto: Record<string, unknown>) {
  const summary = (dto.SummaryData ?? {}) as Record<string, unknown>;
  const history = (dto.History ?? {}) as Record<string, unknown>;
  const img = (dto.ProductImage ?? {}) as Record<string, unknown>;
  const qual = (dto.Qualifications ?? {}) as Record<string, unknown>;
  const env = (dto.EnvironmentalDto ?? {}) as Record<string, unknown>;
  const gidep = (dto.GidepData ?? {}) as Record<string, unknown>;
  const counterfeit = (dto.FullCounterfeitData ?? {}) as Record<string, unknown>;

  const datasheetHistory = asArray<Record<string, unknown>>(history.Datasheet)
    .map(r => ({
      date: String(r.date ?? r.Date ?? ''),
      url: String(r.url ?? r.URL ?? r.Url ?? ''),
    }))
    .filter(d => d.url);

  const lifecycleHistory = asArray<Record<string, unknown>>(history.Lifecycle).map(r => ({
    date: String(r.Date ?? r.date ?? ''),
    lifecycle: String(r.Lifecycle ?? ''),
    manufacturerName: String(r.ManufacturerName ?? ''),
    partNumber: String(r.PartNumber ?? ''),
    reasonOfChange: String(r.ReasonOfChange ?? ''),
    sourceName: String(r.SourceName ?? ''),
    sourceURL: String(r.SourceURL ?? ''),
  }));

  const counterfeitReports = asArray<Record<string, unknown>>(
    counterfeit.ManCounterfeitReports,
  )
    .slice(0, 20)
    .map(r => ({
      mpn: String(r.MPN ?? ''),
      supplier: String(r.Supplier ?? ''),
      notificationDate: String(r.NotificationDate ?? ''),
      description: String(r.Description ?? ''),
      counterfeitMethod: String(r.CounterfitMethods ?? r.CounterfeitMethods ?? ''),
      source: String(r.Source ?? ''),
    }));
  const cfAll = asArray<Record<string, unknown>>(counterfeit.ManCounterfeitReports);
  const cfCount = intFromCount(counterfeit.ManCounterfeitReportsCount) ?? cfAll.length;

  const certSources: Array<[string, unknown, string]> = [
    ['AEC-Q100', summary.AECPDF,
      `AEC number: ${summary.AECNumber ?? '—'} · AEC qualified: ${summary.AECQualified ?? '—'}`],
    ['ISO 26262 (ASIL)', summary.Iso26262Source, 'functional safety'],
    ['IATF 16949', summary.IsoTs16949Source, 'automotive QMS'],
    ['PPAP', summary.PPAPSource, `PPAP status: ${summary.PPAP ?? '—'}`],
    ['Automotive qualification', summary.AutomotiveSource,
      `Automotive: ${summary.Automotive ?? '—'}`],
    ['ESD qualification',
      (qual.ESDQualification as Record<string, unknown>)?.SourceOfInformation
        ?? summary.ESDSourceofInformation,
      String((qual.ESDQualification as Record<string, unknown>)?.ESDClass ?? '')],
    ['Flammability',
      (qual.Flammability as Record<string, unknown>)?.PDFURL,
      String((qual.Flammability as Record<string, unknown>)?.FlammabilityRating ?? '')],
    ['Reliability (FIT/MTBF)',
      (qual.Reliability as Record<string, unknown>)?.SourceOfInformation, ''],
    ['Material declaration (RoHS)', env.Source, String(env.SourceType ?? '—')],
    ['Conflict minerals policy', env.ConflictMineralsPolicy, ''],
    ['Conflict minerals statement', env.ConflictMineralStatement, ''],
    ['CMRT template', env.EICCTemplate, String(env.EICCTemplateVersion ?? '')],
    ['SEC form SD', env.SDForm, ''],
  ];
  const certifications = certSources
    .filter(([, url]) => !!url)
    .map(([name, url, subtitle]) => ({
      name: String(name),
      url: String(url),
      subtitle: String(subtitle || ''),
    }));

  return {
    images: {
      small: String(img.ProductImageSmall ?? summary.SmallImageURL ?? ''),
      large: String(img.ProductImageLarge ?? ''),
    },
    datasheet: {
      latestUrl: String(summary.Datasheet ?? ''),
      supplierUrl: String(summary.OnlineSupplierDatasheetURL ?? ''),
      latestDate: datasheetHistory[0]?.date ?? '',
      revisionCount: datasheetHistory.length,
      history: datasheetHistory,
    },
    certifications,
    lifecycleHistory,
    gidep: Object.keys(gidep).length
      ? {
          typeOfChange: String(gidep.TypeOfChange ?? ''),
          description: String(gidep.GIDEPDescription ?? ''),
          notificationDate: String(gidep.NotificationDate ?? ''),
          documentNumber: String(gidep.DocumentNumber ?? ''),
        }
      : {},
    counterfeit: {
      overallRisk: String(counterfeit.CounterfeitOverallRisk ?? ''),
      overallGrade: String(counterfeit.OverallCounterfeitRiskGrade ?? ''),
      reportsCount: cfCount,
      reports: counterfeitReports,
    },
  };
}

function normalizeRegulatory(dto: Record<string, unknown>) {
  const env = (dto.EnvironmentalDto ?? {}) as Record<string, unknown>;
  const china = (env.ChinaRoHS ?? {}) as Record<string, unknown>;
  const reach = ((dto.ReachData ?? {}) as Record<string, unknown>).ReachDto as Record<string, unknown> | undefined ?? {};
  const annex = (reach.AnnexXIV ?? {}) as Record<string, unknown>;
  const qual = (dto.Qualifications ?? {}) as Record<string, unknown>;
  const esd = (qual.ESDQualification ?? {}) as Record<string, unknown>;
  const flam = (qual.Flammability ?? {}) as Record<string, unknown>;
  const rel = (qual.Reliability ?? {}) as Record<string, unknown>;
  const fit = (rel.FitDetail ?? {}) as Record<string, unknown>;
  const mtbf = (rel.MTBFDetail ?? {}) as Record<string, unknown>;

  const otherSrc = (env.OtherSources ?? {}) as Record<string, unknown>;

  return {
    rohs: {
      status: String(env.RoHSStatus ?? ''),
      version: String(env.RoHSVersion ?? ''),
      source: String(env.Source ?? ''),
      sourceType: String(env.SourceType ?? ''),
      otherSource: String(otherSrc.Source ?? ''),
      exemption: String(env.Exemption ?? ''),
      exemptionType: String(env.ExemptionType ?? ''),
      exemptionCodes: String(env.ExemptionCodes ?? ''),
      leadFree: String(env.LeadFree ?? ''),
    },
    chinaRoHS: {
      status: String(china.ChinaRoHSStatus ?? ''),
      version: String(china.ChinaRoHSVersion ?? ''),
      concentrations: {
        cadmium:  String(china.CadmiumConcentration ?? ''),
        chromium: String(china.ChromiumConcentration ?? ''),
        lead:     String(china.LeadConcentration ?? ''),
        mercury:  String(china.MercuryConcentration ?? ''),
        PBB:      String(china.PBBConcentration ?? ''),
        PBDE:     String(china.PBDEConcentration ?? ''),
        DEHP:     String(china.EthylhexylDehpConcentration ?? ''),
        BBP:      String(china.ButylBenzylBbpConcentration ?? ''),
        DBP:      String(china.DibutylDbpConcentration ?? ''),
      },
      flags: {
        cadmium:  String(china.CadmiumFlag ?? ''),
        chromium: String(china.ChromiumFlag ?? ''),
        lead:     String(china.LeadFlag ?? ''),
        mercury:  String(china.MercuryFlag ?? ''),
        PBB:      String(china.PBBFlag ?? ''),
        PBDE:     String(china.PBDEFlag ?? ''),
        DEHP:     String(china.EthylhexylDehpFlag ?? ''),
        BBP:      String(china.ButylBenzylBbpFlag ?? ''),
        DBP:      String(china.DibutylDbpFlag ?? ''),
      },
    },
    reach: {
      status:             String(reach.ReachStatus ?? ''),
      containsSVHC:       String(reach.ContainsSVHC ?? ''),
      exceedsThreshold:   String(reach.SVHCExceedThresholdLimit ?? ''),
      svhcListVersion:    String(reach.SVHCListVersion ?? ''),
      substance:          String(reach.SubstanceIdentification ?? ''),
      substanceLocation:  String(reach.SubstanceLocation ?? ''),
      concentration:      String(reach.SubstanceConcentration ?? ''),
      casNumber:          String(reach.CASNumber ?? ''),
      inclusionDate:      String(reach.SVHCDateOfInclusion ?? ''),
      sourceType:         String(reach.SourceType ?? ''),
      source:             String(reach.CachedSource ?? ''),
      annexXIV: {
        sunsetDate:       String(annex.SunsetDate ?? ''),
        applicationDate:  String(annex.ApplicationDate ?? ''),
        authEntryNumber:  String(annex.AuthorizationEntryNumber ?? ''),
        exempted:         String(annex.ExemptedCategories ?? ''),
      },
    },
    conflictMinerals: {
      status:          String(env.ConflictMineralStatus ?? ''),
      statement:       String(env.ConflictMineralStatement ?? ''),
      policy:          String(env.ConflictMineralsPolicy ?? ''),
      eiccMembership:  String(env.EICCMembership ?? ''),
      eiccTemplate:    String(env.EICCTemplate ?? ''),
      eiccVersion:     String(env.EICCTemplateVersion ?? ''),
      sdForm:          String(env.SDForm ?? ''),
      sustainability:  String(env.ConflictMineralsSustainabilityReport ?? ''),
    },
    halogen: String(env.HalgonFree ?? env.HalogenFree ?? ''),
    rareEarth: String(env.RareEarthElementInformation ?? ''),
    esd: {
      protection:   String(esd.ESDProtection ?? ''),
      maxVoltage:   String(esd.MaximumESDProtectionVoltage ?? ''),
      esdClass:     String(esd.ESDClass ?? ''),
      source:       String(esd.SourceOfInformation ?? ''),
    },
    flammability: {
      status: String(flam.Flammability ?? ''),
      rating: String(flam.FlammabilityRating ?? ''),
      source: String(flam.PDFURL ?? ''),
    },
    reliability: {
      fit:            String(fit.FIT ?? ''),
      fitCondition:   String(fit.ConditionValue ?? ''),
      mtbf:           String(mtbf.MTBF ?? ''),
      mtbfCondition:  String(mtbf.ConditionValue ?? ''),
      source:         String(rel.SourceOfInformation ?? ''),
      flammabilityRating: String(rel.FlammabilityRating ?? ''),
    },
  };
}

function normalizeChemicals(dto: Record<string, unknown>) {
  const raw = ((dto.ChemicalData ?? {}) as Record<string, unknown>).ChemicalDto;
  const list = asArray<Record<string, unknown>>(raw);
  const comId = String(dto.RequestedComID ?? '');
  return list.map(r => ({
    comId,
    totalMassInGram: toFloat(r.TotalMassInGram),
    totalMassSummationInGram: toFloat(r.TotalMassSummationInGram),
    locationName: String(r.LocationName ?? ''),
    homogenousMaterial: String(r.HomogenousMaterial ?? ''),
    homogenousMaterialMass: toFloat(r.HomogenousMaterialMass),
    substanceIdentification: String(r.SubstanceIdentification ?? ''),
    normalizedSubstance: String(r.NormalizedSubstance ?? ''),
    substanceMass: toFloat(r.SubstanceMass),
    ppm: toFloat(r.PPM),
    casNumber: String(r.CASNumber ?? ''),
    mdsUrl: String(r.MDSURL ?? ''),
    itemSubItem: String(r.ItemSubItem ?? ''),
  }));
}

function normalizeLifecycle(dto: Record<string, unknown> | undefined) {
  const lc = (dto?.LifeCycleData ?? {}) as Record<string, unknown>;
  const risk = (dto?.RiskData ?? {}) as Record<string, unknown>;
  if (!Object.keys(lc).length && !Object.keys(risk).length) return null;

  return {
    partStatus: String(lc.PartStatus ?? ''),
    estimatedYearsToEOL: toFloat(lc.EstimatedYearsToEOL),
    minYearsToEOL: toFloat(lc.MinimumEstimatedYearsToEOL),
    maxYearsToEOL: toFloat(lc.MaximumEstimatedYearsToEOL),
    estimatedEOLDate: String(lc.EstimatedEOLDate ?? ''),
    partLifecycleStage: String(lc.PartLifecycleStage ?? ''),
    lifeCycleRiskGrade: String(lc.LifeCycleRiskGrade ?? ''),
    overallRiskPct: toFloat(lc.OverallRisk),
    lifeCycleComment: String(lc.LifeCycleComment ?? ''),
    riskGrades: {
      rohs: String(risk.RohsRisk ?? ''),
      multiSourcing: String(risk.MultiSourcingRisk ?? ''),
      inventory: String(risk.InventoryRisk ?? ''),
      lifecycle: String(risk.LifecycleRisk ?? ''),
    },
    numberOfDistributors: toFloat(risk.NumberOfDistributors),
    crossesAvailable: String(risk.CrossesPartCategory ?? ''),
  };
}

function normalizePart(dto: Record<string, unknown>, cand: Candidate) {
  const summary = (dto.SummaryData ?? {}) as Record<string, unknown>;
  const env = (dto.EnvironmentalDto ?? {}) as Record<string, unknown>;
  return {
    comId: String(
      (dto.RequestedComID as string) ??
        (summary.DataProviderID as string) ??
        cand.comId ??
        '',
    ),
    pn: cand.pn,
    mpn: cand.mpn || String((summary.PartNumber as string) ?? ''),
    manufacturer: cand.manufacturer || String((summary.Manufacturer as string) ?? ''),
    description: String((summary.PartDescription as string) ?? ''),
    plName: String((summary.PLName as string) ?? ''),
    family: String(
      (summary.FamilyName as string) ?? (summary.GenericName as string) ?? '',
    ),
    taxonomy: String((summary.TaxonomyPath as string) ?? ''),
    datasheetUrl: String((summary.Datasheet as string) ?? ''),
    supplierDatasheetUrl: String((summary.OnlineSupplierDatasheetURL as string) ?? ''),
    imageUrl: String((summary.SmallImageURL as string) ?? ''),
    introductionDate: String((summary.IntroductionDate as string) ?? ''),
    lastCheckDate: String((summary.LastCheckDate as string) ?? ''),
    eccn: String((summary.ECCN as string) ?? ''),
    rohs: String((env.RoHSStatus as string) ?? (summary.EURoHS as string) ?? ''),
    rohsIdentifier: String((env.RohsIdentifier as string) ?? ''),
    conflictMinerals: String((env.ConflictMineralStatus as string) ?? ''),
    automotive: String((summary.Automotive as string) ?? ''),
    aecQualified: String((summary.AECQualified as string) ?? ''),
  };
}

export async function detail(opts: { pn?: string | null; comId?: string | null }) {
  const cand = findCandidate(opts.pn, opts.comId);
  if (!cand) {
    return {
      status: 'not_found',
      reason: `'${opts.pn || opts.comId}' not found in Excel mapping and not in Denodo iv_plm_allparts_latest`,
      part: {},
      lifecycle: null,
      commercial: {},
      parametric: [],
      regulatory: {},
      chemicals: [],
      documents: {},
      packaging: {},
      countries: [],
      raw: { query: opts },
    };
  }

  let com = cand.comId;
  if (!com && cand.mpn) {
    const resolved = await se.resolveComId(cand.mpn, cand.manufacturer || undefined);
    if (resolved) {
      com = resolved;
      cand.comId = resolved;
      cand.source = `${cand.source}+partsearch`;
    }
  }

  const raw: Record<string, unknown> = { candidate: cand };
  let part = normalizePart({}, cand);
  part.comId = com || part.comId;
  let lifecycle: ReturnType<typeof normalizeLifecycle> = null;
  let commercial: ReturnType<typeof normalizeCommercial> | {} = {};
  let parametric: ReturnType<typeof normalizeParametric> = [];
  let regulatory: ReturnType<typeof normalizeRegulatory> | {} = {};
  let chemicals: ReturnType<typeof normalizeChemicals> = [];
  let documents: ReturnType<typeof normalizeDocuments> | {} = {};
  let packaging: ReturnType<typeof normalizePackaging> | {} = {};
  let countries: ReturnType<typeof normalizeCountries> = [];
  let status = 'ok';
  let reason = '';

  if (!com) {
    status = 'no_comid';
    reason = reasonForNoComid(cand);
  } else {
    try {
      const resp = await se.partDetail([com], { lifecycle: true });
      const dtos = (resp as { Results?: { ResultDto?: unknown[] } })?.Results?.ResultDto ?? [];
      raw.partDetail = dtos;
      if (dtos.length && typeof dtos[0] === 'object' && dtos[0]) {
        const d0 = dtos[0] as Record<string, unknown>;
        part = normalizePart(d0, cand);
        lifecycle = normalizeLifecycle(d0);
        commercial = normalizeCommercial(d0);
        parametric = normalizeParametric(d0);
        regulatory = normalizeRegulatory(d0);
        chemicals = normalizeChemicals(d0);
        documents = normalizeDocuments(d0);
        packaging = normalizePackaging(d0);
        countries = normalizeCountries(d0);
      } else {
        status = 'empty_partdetail';
        reason = `ComID ${com} returned no data from /partDetail`;
      }
    } catch (err) {
      raw.error = String(err);
      status = 'partdetail_error';
      reason = `SiliconExpert /partDetail failed · ${String(err)}`;
    }
  }

  // Telemetry — record a high-signal "detail" event with lifecycle context.
  try {
    const lc = (lifecycle ?? {}) as Record<string, unknown>;
    recent.record({
      pn: (part.pn || cand.pn || '').trim(),
      mpn: part.mpn || cand.mpn || '',
      manufacturer: part.manufacturer || cand.manufacturer || '',
      comId: part.comId || cand.comId || '',
      lifecycle: String(lc.partStatus ?? ''),
      yeol: typeof lc.estimatedYearsToEOL === 'number'
        ? (lc.estimatedYearsToEOL as number)
        : null,
      risk: typeof lc.overallRiskPct === 'number' ? (lc.overallRiskPct as number) : null,
      source: cand.source || '',
      kind: 'detail',
    });
  } catch { /* fail-soft */ }

  return {
    status, reason, part, lifecycle, commercial, parametric,
    regulatory, chemicals, documents, packaging, countries, raw,
  };
}

function reasonForNoComid(cand: Candidate & { reason?: string }): string {
  if (cand.reason) return cand.reason;
  if (cand.source?.includes('denodo')) {
    return 'Part found in Advantech Denodo (iv_plm_allparts_latest) but not mapped to a SiliconExpert ComID · no SE data available';
  }
  if (cand.mpn) {
    return `No SE_ComID in Excel and /partsearch returned no match for MPN '${cand.mpn}'` +
      (cand.manufacturer ? ` · ${cand.manufacturer}` : '');
  }
  return 'No SE_ComID in Excel and no MPN to resolve · no SE data available';
}
