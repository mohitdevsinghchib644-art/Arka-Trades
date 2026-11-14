"""
Sector universe. Three separate kinds, never mixed without saying so:

  OFFICIAL   NSE sectoral indices: official level series, official constituents and free-float weights.
             Constituent lists must be loaded from NSE's index files (ind_<index>list.csv) with their
             effective date. This file holds only the registry; it does not hard-code members.
  CUSTOM     Arka's ~50 rotation groups from sector_map.py (equal-weight baskets built from stocks).
  THEMATIC   NSE thematic indices (PSE, CPSE, MNC, Consumption, Commodities...). They overlap sectors.

Every Sector carries id, name, kind, index symbol, benchmark, members, weights, effective date, source,
last update. A member list without an effective date is marked "membership date: NOT AVAILABLE", and any
history built from it is today's list applied backwards (survivorship bias) and is labelled as such.

Index tickers: the Yahoo symbols are the ones the existing Arka code already uses. Before Phase 6 each one
must be checked against NSE's own historical index data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

OFFICIAL_SECTOR_INDICES = {
    # id: (NSE index name, Yahoo symbol or None, NSE constituent file slug)
    "NIFTY_BANK": ("Nifty Bank", "^NSEBANK", "ind_niftybanklist"),
    "NIFTY_PSU_BANK": ("Nifty PSU Bank", "^CNXPSUBANK", "ind_niftypsubanklist"),
    "NIFTY_PVT_BANK": ("Nifty Private Bank", "NIFTY_PVT_BANK.NS", "ind_nifty_privatebanklist"),
    "NIFTY_FIN_SERVICE": ("Nifty Financial Services", "NIFTY_FIN_SERVICE.NS", "ind_niftyfinancelist"),
    "NIFTY_FIN_EXBANK": ("Nifty Financial Services Ex-Bank", None, "ind_niftyfinancialservicesexbank_list"),
    "NIFTY_MS_FIN": ("Nifty MidSmall Financial Services", None, "ind_niftymidsmallfinancailservice_list"),
    "NIFTY_CAPITAL_MKTS": ("Nifty Capital Markets", None, "ind_niftyCapitalMarkets_list"),
    "NIFTY_IT": ("Nifty IT", "^CNXIT", "ind_niftyitlist"),
    "NIFTY_AUTO": ("Nifty Auto", "^CNXAUTO", "ind_niftyautolist"),
    "NIFTY_PHARMA": ("Nifty Pharma", "^CNXPHARMA", "ind_niftypharmalist"),
    "NIFTY_HEALTHCARE": ("Nifty Healthcare", "NIFTY_HEALTHCARE.NS", "ind_niftyhealthcarelist"),
    "NIFTY_FMCG": ("Nifty FMCG", "^CNXFMCG", "ind_niftyfmcglist"),
    "NIFTY_CONSR_DURBL": ("Nifty Consumer Durables", "NIFTY_CONSR_DURBL.NS", "ind_niftyconsumerdurableslist"),
    "NIFTY_REALTY": ("Nifty Realty", "^CNXREALTY", "ind_niftyrealtylist"),
    "NIFTY_METAL": ("Nifty Metal", "^CNXMETAL", "ind_niftymetallist"),
    "NIFTY_OIL_GAS": ("Nifty Oil & Gas", "NIFTY_OIL_AND_GAS.NS", "ind_niftyoilgaslist"),
    "NIFTY_ENERGY": ("Nifty Energy", "^CNXENERGY", "ind_niftyenergylist"),
    "NIFTY_INFRA": ("Nifty Infrastructure", "^CNXINFRA", "ind_niftyinfralist"),
    "NIFTY_MEDIA": ("Nifty Media", "^CNXMEDIA", "ind_niftymedialist"),
}
THEMATIC_INDICES = {
    "NIFTY_PSE": ("Nifty PSE", "^CNXPSE", "ind_niftypselist"),
    "NIFTY_CPSE": ("Nifty CPSE", None, "ind_niftycpselist"),
    "NIFTY_COMMODITIES": ("Nifty Commodities", "^CNXCMDT", "ind_niftycommoditieslist"),
    "NIFTY_CONSUMPTION": ("Nifty India Consumption", "^CNXCONSUM", "ind_niftyconsumptionlist"),
    "NIFTY_MNC": ("Nifty MNC", "^CNXMNC", "ind_niftymnclist"),
}
BENCHMARKS = {"NIFTY 50": "^NSEI", "NIFTY 500": "^CRSLDX"}
DEFAULT_BENCHMARK = "NIFTY 500"
# UNVERIFIED: the file slugs above follow NSE's naming pattern, which is irregular. Check each against
# niftyindices.com before use. A slug that fails to load must be reported as "constituents NOT AVAILABLE".


@dataclass
class Sector:
    sector_id: str
    name: str
    kind: str                         # OFFICIAL | CUSTOM | THEMATIC
    index_symbol: str | None
    benchmark: str
    members: list = field(default_factory=list)
    weights: dict = field(default_factory=dict)     # symbol -> % (official only, when NSE weights are loaded)
    membership_date: str | None = None
    source: str = ""
    last_update: str | None = None

    @property
    def survivorship_note(self) -> str | None:
        if self.membership_date is None and self.members:
            return "today's member list applied to history: survivorship bias, not point-in-time"
        return None


def official_sectors(benchmark: str = DEFAULT_BENCHMARK, include_thematic: bool = False) -> list:
    reg = {**OFFICIAL_SECTOR_INDICES, **(THEMATIC_INDICES if include_thematic else {})}
    return [Sector(k, name, "THEMATIC" if k in THEMATIC_INDICES else "OFFICIAL", sym, benchmark,
                   source=f"NSE index file {slug}.csv (not loaded)") for k, (name, sym, slug) in reg.items()]


def attach_constituents(sector: Sector, csv_text: str, effective_date: str | None, loaded_at: str) -> Sector:
    """Load an NSE ind_*list.csv into an official sector. NSE's file has no weights; weights stay empty."""
    import io

    import pandas as pd
    df = pd.read_csv(io.StringIO(csv_text))
    df.columns = [c.strip() for c in df.columns]
    if "Symbol" not in df:
        raise ValueError("not an NSE constituent file (no Symbol column)")
    sector.members = df["Symbol"].astype(str).str.strip().str.upper().tolist()
    sector.membership_date = effective_date
    sector.last_update = loaded_at
    sector.source = sector.source.replace("(not loaded)", "(loaded)")
    return sector


def custom_sectors(benchmark: str = DEFAULT_BENCHMARK, min_members: int = 3) -> list:
    """Arka's rotation groups from the existing sector_map.py (imported, not copied)."""
    import sector_map as smap
    out = []
    for g, (fam, syms) in smap.GROUPS.items():
        m = syms.split()
        if len(m) >= min_members:
            out.append(Sector("CUSTOM_" + "".join(ch if ch.isalnum() else "_" for ch in g.upper()), g, "CUSTOM", None,
                              benchmark, members=m, source="sector_map.py GROUPS (curated, no effective dates)"))
    return out
