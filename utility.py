"""Download and read SEC EDGAR ABS-EE asset-level filings.

Modernized replacement for the original 2017 utility.py. Drops the legacy
``srch-edgar`` RSS / beautifulscraper / feedparser stack in favor of the
modern EDGAR full-text search JSON API and a single requests session.

Public surface kept compatible with the original parser-side workflow:
    - read_ald_xml(path)              # parse one XML to a DataFrame
    - read_ald_files(df, etype, acls) # concat all matching XMLs
    - pickle_save(obj, name) / pickle_load(names)
    - read_listing(path)              # load a listing CSV

New CLI:
    python utility.py search   --start YYYY-MM-DD --end YYYY-MM-DD [--out Inputs/dtABS.csv]
    python utility.py download [--listing Inputs/dtABS.csv] [--entities Trust ...] [--assets "Auto Loans" ...]
"""
from __future__ import annotations

import argparse
import logging
import pickle
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
import xmltodict
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "Inputs"
PICKLED = ROOT / "Pickled"

# SEC requires a descriptive User-Agent identifying the requester.
# https://www.sec.gov/os/accessing-edgar-data
USER_AGENT = "Rivet ABS-EE Tools peter@rivet.fi"

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# SEC fair-use limit is 10 req/sec; sleep 0.12s between calls.
_REQUEST_INTERVAL_SEC = 0.12
# EFTS caps pagination at 10,000 results regardless of total.
_EFTS_MAX_OFFSET = 10_000

log = logging.getLogger("absee")


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Accept-Encoding"] = "gzip, deflate"
    return s


# ---------------------------------------------------------------------------
# Classification heuristics (preserved from the original code)
# ---------------------------------------------------------------------------

def classify_asset(secname: str) -> str:
    name = secname.lower()
    if "leas" in name:
        return "Auto Leases"
    if "mortgage" in name or "stanley" in name or "bnk4" in name:
        return "CMBS"
    return "Auto Loans"


def classify_entity(secname: str) -> str:
    return "Trust" if "trust" in secname.lower() else "Depositor"


def _strip_cik_suffix(display_name: str) -> str:
    """`"Ally Auto Receivables Trust 2024-1 (CIK 0001234567) (Filer)"` -> name."""
    return display_name.split(" (CIK")[0].strip()


def _safe_filename(secname: str, report_date: str, accession: str) -> str:
    return f"{secname.replace(' ', '_')}_{report_date}_{accession}.xml"


def _derive_shelf(secname: str) -> str:
    """Strip a 4-digit year onward to get the shelf name."""
    m = re.search(r"\b(19|20)\d{2}\b", secname)
    return secname[: m.start()].rstrip(" -") if m else secname


# ---------------------------------------------------------------------------
# EDGAR search
# ---------------------------------------------------------------------------

@dataclass
class FilingRow:
    secname: str
    filename: str
    entitytype: str
    assetclass: str
    reportdate: str
    url: str


def _iter_search_hits(
    session: requests.Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    ciks: list[str] | None = None,
) -> Iterable[dict]:
    """Page through EDGAR full-text search for ABS-EE filings.

    Either a date range or a CIK list (or both) may be supplied. With neither,
    EDGAR returns all ABS-EE filings (capped at 10,000 results).
    """
    offset = 0
    while offset < _EFTS_MAX_OFFSET:
        params: dict = {"q": "", "forms": "ABS-EE", "from": offset}
        if start_date and end_date:
            params["dateRange"] = "custom"
            params["startdt"] = start_date
            params["enddt"] = end_date
        if ciks:
            # EFTS requires zero-padded 10-digit CIKs.
            params["ciks"] = ",".join(str(int(c)).zfill(10) for c in ciks)
        r = session.get(EDGAR_SEARCH_URL, params=params, timeout=60)
        r.raise_for_status()
        body = r.json()
        hits = body.get("hits", {}).get("hits", []) or []
        total = body.get("hits", {}).get("total", {}).get("value", 0)
        if not hits:
            return
        for h in hits:
            yield h
        offset += len(hits)
        if offset >= total:
            return
        time.sleep(_REQUEST_INTERVAL_SEC)
    log.warning(
        "EDGAR full-text search hit the %d-result cap; narrow your query.",
        _EFTS_MAX_OFFSET,
    )


def _list_filing_files(cik: str, accession_nodash: str, session: requests.Session) -> list[dict]:
    url = f"{EDGAR_ARCHIVES_BASE}/{int(cik)}/{accession_nodash}/index.json"
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.json().get("directory", {}).get("item", [])


def _pick_asset_level_xml(items: list[dict]) -> str | None:
    """Pick the asset-level XML (EX-102 exhibit) from a filing's file list.

    Filings include the 102 (asset data) and a much smaller 103 (asset
    tagging) exhibit, plus an HTML cover. EX-102 is always the largest XML
    and never has "103" in its name.
    """
    candidates = []
    for it in items:
        name = it.get("name", "")
        if not name.lower().endswith(".xml"):
            continue
        if "103" in name:
            continue
        if name.endswith(".xsd"):
            continue
        try:
            size = int(it.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        candidates.append((size, name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


_LISTING_COLUMNS = ["secname", "filename", "entitytype", "assetclass", "reportdate", "url"]
_CIK_URL_RE = re.compile(r"/edgar/data/(\d+)/")


def _shelf_key(secname: str) -> str:
    """Case-insensitive comparison key for a securitization shelf."""
    return _derive_shelf(secname).casefold()


def _extract_cik_from_url(url: str) -> str | None:
    m = _CIK_URL_RE.search(str(url))
    return m.group(1) if m else None


def _filing_rows_from_hit(hit: dict, session: requests.Session) -> list[FilingRow]:
    """Resolve a single EFTS hit into one FilingRow per filer (or [] on failure)."""
    source = hit.get("_source", {})
    adsh = source.get("adsh") or str(hit.get("_id", "")).split(":")[0]
    if not adsh:
        return []
    accession_nodash = adsh.replace("-", "")
    ciks = source.get("ciks") or []
    display_names = source.get("display_names") or []
    file_date = source.get("file_date", "")
    report_date = file_date.replace("-", "")
    if not ciks or not display_names:
        return []
    # EFTS returns co-filer CIKs in a different order depending on which CIK
    # was used as the search filter. We need a stable canonical CIK so the
    # same filing always yields the same URL. The lowest CIK is the earliest
    # registrant (the depositor), which matches the URL form SEC itself uses
    # in filing indexes.
    canonical_cik = min(int(c) for c in ciks)
    try:
        items = _list_filing_files(str(canonical_cik), accession_nodash, session)
    except requests.HTTPError as e:
        log.warning("  index.json failed for %s: %s", adsh, e)
        return []
    xml_name = _pick_asset_level_xml(items)
    if not xml_name:
        log.warning("  no asset-level XML in %s", adsh)
        return []
    url = f"{EDGAR_ARCHIVES_BASE}/{canonical_cik}/{accession_nodash}/{xml_name}"
    rows: list[FilingRow] = []
    for name in display_names:
        secname = _strip_cik_suffix(name)
        rows.append(FilingRow(
            secname=secname,
            filename=_safe_filename(secname, report_date, adsh),
            entitytype=classify_entity(secname),
            assetclass=classify_asset(secname),
            reportdate=report_date,
            url=url,
        ))
    return rows


def _normalize_listing(df: pd.DataFrame) -> pd.DataFrame:
    """Case-insensitive dedup on (url, secname), keeping a non-all-uppercase casing.

    EDGAR occasionally reports the same shelf in screaming caps (e.g. ``ALLY
    AUTO RECEIVABLES TRUST``) in some filings and title case in others. We
    fold those together so a shelf isn't double-counted.
    """
    if df.empty:
        return df.reset_index(drop=True)
    work = df.copy()
    work["reportdate"] = work["reportdate"].astype(str)
    # Canonicalize secname to its preferred casing across the whole frame.
    preferred: dict[str, str] = {}
    for s in work["secname"]:
        cf = s.casefold()
        cur = preferred.get(cf)
        if cur is None or (cur.isupper() and not s.isupper()):
            preferred[cf] = s
    work["secname"] = work["secname"].map(lambda s: preferred[s.casefold()])
    # Re-derive filename so it matches the canonical secname.
    work["filename"] = [
        _safe_filename(row.secname, str(row.reportdate),
                       _accession_from_filename(row.filename) or "")
        for row in work.itertuples()
    ]
    work = work.drop_duplicates(["url", "secname"], keep="first").reset_index(drop=True)
    return work[_LISTING_COLUMNS]


def _accession_from_filename(filename: str) -> str | None:
    m = re.search(r"(\d{10}-\d{2}-\d{6})", filename)
    return m.group(1) if m else None


def _summarize_diff(
    before: pd.DataFrame | None, after: pd.DataFrame
) -> tuple[int, int, list[str]]:
    """(net-new rows, net-new trusts, new shelf display names)."""
    def _trust_keys(df: pd.DataFrame) -> set[str]:
        return set(df.loc[df["entitytype"] == "Trust", "secname"].str.casefold())

    def _shelf_keys(df: pd.DataFrame) -> set[str]:
        return {_shelf_key(s) for s in df.loc[df["entitytype"] == "Trust", "secname"]}

    if before is None or before.empty:
        before_row_keys: set[tuple] = set()
        before_t = set()
        before_s = set()
    else:
        before_row_keys = set(zip(before["url"], before["secname"].str.casefold()))
        before_t = _trust_keys(before)
        before_s = _shelf_keys(before)

    after_row_keys = set(zip(after["url"], after["secname"].str.casefold()))
    after_t = _trust_keys(after)
    after_s = _shelf_keys(after)

    n_new_rows = len(after_row_keys - before_row_keys)
    n_new_trusts = len(after_t - before_t)

    new_shelf_keys = after_s - before_s
    after_trust_names = after.loc[after["entitytype"] == "Trust", "secname"]
    by_key: dict[str, str] = {}
    for s in after_trust_names:
        k = _shelf_key(s)
        if k not in new_shelf_keys:
            continue
        cand = _derive_shelf(s)
        cur = by_key.get(k)
        if cur is None or (cur.isupper() and not cand.isupper()):
            by_key[k] = cand
    new_shelves = sorted(by_key.values())
    return n_new_rows, n_new_trusts, new_shelves


def _load_existing_listing(out_path: Path) -> pd.DataFrame | None:
    if not out_path.exists():
        return None
    try:
        return pd.read_csv(out_path, dtype={"reportdate": str})
    except Exception as e:
        log.warning("Could not read existing listing %s (%s); will overwrite.", out_path, e)
        return None


def _commit_listing(
    out_path: Path,
    new_rows: list[FilingRow],
    existing: pd.DataFrame | None,
    *,
    headline: str,
) -> pd.DataFrame:
    """Merge ``new_rows`` into ``existing``, normalize casing, write CSV, log diff."""
    new_df = pd.DataFrame([asdict(r) for r in new_rows], columns=_LISTING_COLUMNS)
    if existing is not None and not existing.empty:
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    final = _normalize_listing(combined)

    n_new_rows, n_new_trusts, new_shelves = _summarize_diff(existing, final)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.to_csv(out_path, index=False)

    log.info(headline)
    log.info("  +%d net-new rows; +%d new trusts; +%d new shelves.",
             n_new_rows, n_new_trusts, len(new_shelves))
    if new_shelves:
        log.info("  New shelves: %s", new_shelves)
    log.info("Listing now has %d total entries at %s", len(final), out_path)
    return final


def absee_search(
    start_date: str,
    end_date: str,
    output_path: Path | str,
    *,
    fresh: bool = False,
) -> pd.DataFrame:
    """Search EDGAR for ABS-EE filings in a date range and write a listing CSV.

    By default, merges into an existing listing at ``output_path`` so repeated
    runs accumulate every securitization EDGAR has ever published an ABS-EE
    for. Pass ``fresh=True`` to overwrite instead.

    Args:
        start_date, end_date: ISO 'YYYY-MM-DD'.
        output_path: where to write the CSV (relative paths land under Inputs/).
        fresh: if True, ignore any existing CSV and overwrite it.
    """
    out = Path(output_path)
    if not out.is_absolute():
        out = ROOT / out

    existing = None if fresh else _load_existing_listing(out)
    if existing is not None:
        log.info("Merging into existing listing: %d entries in %s", len(existing), out)

    session = _session()
    rows: list[FilingRow] = []
    n_filings = 0

    log.info("Searching EDGAR for ABS-EE filings %s -> %s", start_date, end_date)
    for hit in _iter_search_hits(session, start_date=start_date, end_date=end_date):
        n_filings += 1
        rows.extend(_filing_rows_from_hit(hit, session))
        time.sleep(_REQUEST_INTERVAL_SEC)

    return _commit_listing(
        out, rows, existing,
        headline=f"Search returned {n_filings} filings, {len(rows)} filer-rows.",
    )


def absee_history(
    listing_path: Path | str,
    *,
    shelf_pattern: str | None = None,
    cik_filter: list[str] | None = None,
    asset_classes: list[str] | None = None,
) -> pd.DataFrame:
    """Backfill the listing with the *full* ABS-EE history for selected CIKs.

    Resolves which CIKs to backfill by either (a) the explicit ``cik_filter``
    list, or (b) extracting them from rows of the existing listing that match
    ``shelf_pattern`` (case-insensitive regex) and ``asset_classes``. For each
    selected CIK, queries EDGAR full-text search with no date bound so every
    ABS-EE that filer has ever been listed on is enumerated, then merges any
    previously-unseen filings into the listing.

    Args:
        listing_path: existing listing CSV. Also used as the merge target.
        shelf_pattern: regex matched against ``secname`` (case-insensitive)
            to filter which existing listings supply seed CIKs.
        cik_filter: bypass the listing and backfill these CIKs directly.
        asset_classes: further restrict the seed by asset class.
    """
    out = Path(listing_path)
    if not out.is_absolute():
        out = ROOT / out
    existing = _load_existing_listing(out)
    if existing is None and not cik_filter:
        raise FileNotFoundError(
            f"No listing at {out} to extract CIKs from. Run `search` first, "
            "or pass --cik explicitly."
        )

    ciks: set[str] = set()
    cik_to_seed_names: dict[str, set[str]] = {}
    if cik_filter:
        ciks.update(str(int(c)) for c in cik_filter)
    else:
        seed = existing.copy()
        if shelf_pattern:
            mask = seed["secname"].str.contains(shelf_pattern, case=False, regex=True, na=False)
            seed = seed[mask]
        if asset_classes:
            seed = seed[seed["assetclass"].isin(asset_classes)]
        if seed.empty:
            log.warning("No rows matched the filter; nothing to backfill.")
            return existing if existing is not None else pd.DataFrame(columns=_LISTING_COLUMNS)
        for _, row in seed.iterrows():
            cik = _extract_cik_from_url(row["url"])
            if not cik:
                continue
            ciks.add(cik)
            cik_to_seed_names.setdefault(cik, set()).add(row["secname"])

    log.info("Backfilling ABS-EE history for %d CIK(s) ...", len(ciks))
    session = _session()
    rows: list[FilingRow] = []
    for cik in sorted(ciks, key=lambda c: int(c)):
        label = ", ".join(sorted(cik_to_seed_names.get(cik, set()))[:1]) or "(no seed name)"
        n_for_cik = 0
        for hit in _iter_search_hits(session, ciks=[cik]):
            new = _filing_rows_from_hit(hit, session)
            rows.extend(new)
            n_for_cik += 1
            time.sleep(_REQUEST_INTERVAL_SEC)
        log.info("  CIK %s [%s]: %d filing(s)", cik, label, n_for_cik)

    return _commit_listing(
        out, rows, existing,
        headline=f"History backfill processed {len(ciks)} CIK(s), {len(rows)} filer-rows.",
    )


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def read_listing(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() and not p.is_absolute():
        p = INPUTS / p.name
    df = pd.read_csv(p)
    log.info("Loaded %d listings from %s", len(df), p)
    return df


def download_filings(
    df: pd.DataFrame,
    entity_types: list[str] | None = None,
    asset_classes: list[str] | None = None,
    *,
    overwrite: bool = False,
) -> None:
    """Download XML filings selected from a listing DataFrame.

    Files are saved under ``<asset_class>/<filename>``. Existing files are
    skipped unless ``overwrite=True``. Downloads are streamed to a ``.part``
    file and atomically renamed on success.
    """
    if entity_types is None:
        entity_types = sorted(df["entitytype"].unique().tolist())
    if asset_classes is None:
        asset_classes = sorted(df["assetclass"].unique().tolist())

    mask = df["entitytype"].isin(entity_types) & df["assetclass"].isin(asset_classes)
    selected = df.loc[mask]
    log.info("Selected %d/%d filings for download.", len(selected), len(df))

    session = _session()
    seen_urls: set[str] = set()
    for _, row in selected.iterrows():
        url = row["url"]
        if url in seen_urls:
            continue  # depositor + trust share the same XML
        seen_urls.add(url)

        out_dir = ROOT / row["assetclass"]
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / row["filename"]
        if dest.exists() and dest.stat().st_size > 0 and not overwrite:
            log.info("Already on disk: %s", dest.name)
            continue

        log.info("Downloading %s ...", dest.name)
        try:
            r = session.get(url, stream=True, timeout=300)
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=128 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp.replace(dest)
            log.info("  saved (%s bytes)", f"{dest.stat().st_size:,}")
        except Exception as e:
            log.warning("  FAILED: %s", e)
        time.sleep(_REQUEST_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# XML / pickle helpers (kept for parser-side workflows)
# ---------------------------------------------------------------------------

def read_ald_xml(path: str | Path) -> pd.DataFrame:
    """Parse one asset-level XML file into a DataFrame."""
    with open(path) as fh:
        doc = xmltodict.parse(fh.read())
    return pd.DataFrame.from_dict(doc["assetData"]["assets"])


def read_ald_files(
    df: pd.DataFrame,
    entity_type: str,
    asset_class: str,
    *,
    keep_cols: set[str] | None = None,
    numeric_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Read and concatenate all XMLs matching (entity_type, asset_class).

    Optional memory controls (default off → byte-for-byte the original behavior;
    the reporting/vetting path in ``main.py`` calls this without them). They let
    the subprime-index build avoid materializing a multi-GB *all-object* frame on
    a small-RAM machine, which otherwise swaps and turns a ~90 s coercion into
    >80 min:

      * ``keep_cols`` — drop every XML column not in this set as each file is
        read. The injected ``securitizationKey`` / ``shelf`` / ``reportDate``
        always survive (they are added after the prune).
      * ``numeric_cols`` — coerce these columns to numeric *per file*, before
        concatenation, so the concatenated intermediate is float64 (8 B/value)
        rather than object strings (~50 B/value). ``to_numeric`` is row-wise and
        idempotent, so a later ``clean_ald_files`` coercion is a no-op and the
        enriched output is identical to coercing after the concat.
    """
    mask = (df["entitytype"] == entity_type) & (df["assetclass"] == asset_class)
    frames: list[pd.DataFrame] = []
    keep = set(keep_cols) if keep_cols is not None else None
    for _, row in df.loc[mask].iterrows():
        path = ROOT / row["assetclass"] / row["filename"]
        if not path.exists():
            log.warning("Missing file: %s", path)
            continue
        log.info("Reading %s ...", path.name)
        temp = read_ald_xml(path)
        if keep is not None:
            temp = temp[[c for c in temp.columns if c in keep]]
        if numeric_cols:
            for c in numeric_cols:
                if c in temp.columns:
                    temp[c] = pd.to_numeric(temp[c], errors="coerce")
        temp["securitizationKey"] = row["secname"]
        temp["shelf"] = _derive_shelf(row["secname"])
        temp["reportDate"] = row["reportdate"]
        frames.append(temp)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=0, ignore_index=True)


def pickle_save(obj, name: str) -> None:
    PICKLED.mkdir(parents=True, exist_ok=True)
    path = PICKLED / name
    with open(path, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    log.info("Pickled to %s", path)


def pickle_load(names: list[str]) -> pd.DataFrame:
    frames = []
    for name in names:
        path = PICKLED / name
        log.info("Loading pickle %s", path)
        with open(path, "rb") as f:
            frames.append(pickle.load(f))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=0, ignore_index=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download SEC EDGAR ABS-EE filings.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser(
        "search",
        help="Search EDGAR for ABS-EE filings. Merges into existing --out by default.",
    )
    p_search.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p_search.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p_search.add_argument("--out", default="Inputs/dtABS.csv", help="Output CSV path")
    p_search.add_argument("--fresh", action="store_true",
                          help="Overwrite the listing instead of merging.")

    p_hist = sub.add_parser(
        "history",
        help="Backfill ABS-EE history for CIKs already in the listing (or specific --cik(s)).",
    )
    p_hist.add_argument("--listing", default="Inputs/dtABS.csv", help="Listing CSV path")
    p_hist.add_argument("--shelf", default=None,
                        help="Case-insensitive regex; restricts which existing rows seed CIKs.")
    p_hist.add_argument("--cik", nargs="+", default=None,
                        help="Specific CIK(s) to backfill, bypassing the listing.")
    p_hist.add_argument("--asset-class", nargs="+", default=None,
                        help="Restrict seed rows to these asset classes (Auto Loans / Auto Leases / CMBS).")

    p_dl = sub.add_parser("download", help="Download XML filings from a listing CSV.")
    p_dl.add_argument("--listing", default="Inputs/dtABS.csv", help="Listing CSV path")
    p_dl.add_argument("--entities", nargs="+", default=["Trust"],
                      help="Entity types to download (default: Trust)")
    p_dl.add_argument("--assets", nargs="+", default=["Auto Loans", "Auto Leases"],
                      help="Asset classes to download (default: Auto Loans, Auto Leases)")
    p_dl.add_argument("--overwrite", action="store_true", help="Re-download existing files")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    if args.cmd == "search":
        absee_search(args.start, args.end, args.out, fresh=args.fresh)
    elif args.cmd == "history":
        absee_history(
            args.listing,
            shelf_pattern=args.shelf,
            cik_filter=args.cik,
            asset_classes=args.asset_class,
        )
    elif args.cmd == "download":
        df = read_listing(args.listing)
        download_filings(df, args.entities, args.assets, overwrite=args.overwrite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
