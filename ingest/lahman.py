"""Lahman Baseball Database ingest.

Downloads the CSV snapshot once into data/raw/lahman/ and loads it into
the project's DuckDB file. Re-running this script does NOT re-download
data that is already present on disk -- pass --force to refresh.

Primary source: SABR (Society for American Baseball Research), which has
taken over maintenance of the Lahman Baseball Database and publishes a CSV
snapshot (covering through the 2025 season, released January 2026) as a
public Box.com folder:
    https://sabr.box.com/s/y1prhc795jk8zvmelfd3jq7tl389y6cd
    (folder name: lahman_1871-2025_csv)

That folder has no explicit license notice on the page itself (only the
Negro Leagues data elsewhere on SABR's site is explicitly credited to
Seamheads.com); this ingest is intended for personal/research use.

Box.com share pages are a JS-rendered SPA with no public listing API, so
we resolve the folder's file list by parsing the `Box.postStreamData`
JSON Box embeds in the share page's HTML (paginated -- we walk every
page), then download each CSV via Box's direct shared-file endpoint
(`index.php?rm=box_download_shared_file`), which redirects to a
public.boxcloud.com URL. This is inherently a bit more fragile than a
documented API (Box could change the share-page markup), so failures here
should be diagnosed by re-fetching PRIMARY_FOLDER_URL in a browser first.

Fallback source: the previous data source (a GitHub mirror of the old
chadwickbureau/baseballdatabank repo, which was taken down upstream) is
kept as a fallback in case the SABR Box folder becomes unavailable. Note
that mirror's snapshot only covers through the 2021 season.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "lahman"
DB_PATH = ROOT / "data" / "processed" / "mlb.duckdb"

SABR_SHARED_NAME = "y1prhc795jk8zvmelfd3jq7tl389y6cd"
SABR_SHARE_URL = f"https://sabr.app.box.com/s/{SABR_SHARED_NAME}"
SABR_DOWNLOAD_URL = "https://sabr.app.box.com/index.php"

FALLBACK_SOURCE_URL = "https://github.com/xorq-labs/baseballdatabank/archive/refs/heads/master.zip"

# CSV filename (as published in the SABR Box folder) -> DuckDB table name.
WANTED_FILES = {
    "People.csv": "people",
    "Batting.csv": "batting",
    "Pitching.csv": "pitching",
    "Fielding.csv": "fielding",
    "BattingPost.csv": "batting_post",
    "PitchingPost.csv": "pitching_post",
    "FieldingPost.csv": "fielding_post",
    "Appearances.csv": "appearances",
    "AllstarFull.csv": "allstar_full",
    "Teams.csv": "teams",
    "TeamsFranchises.csv": "teams_franchises",
    "AwardsPlayers.csv": "awards_players",
    "HallOfFame.csv": "hall_of_fame",
}

# Fallback mirror uses the old core/ + contrib/ chadwickbureau layout.
FALLBACK_WANTED_FILES = {
    "core/People.csv": "people",
    "core/Batting.csv": "batting",
    "core/Pitching.csv": "pitching",
    "core/Fielding.csv": "fielding",
    "core/BattingPost.csv": "batting_post",
    "core/PitchingPost.csv": "pitching_post",
    "core/FieldingPost.csv": "fielding_post",
    "core/Appearances.csv": "appearances",
    "core/AllstarFull.csv": "allstar_full",
    "core/Teams.csv": "teams",
    "core/TeamsFranchises.csv": "teams_franchises",
    "contrib/AwardsPlayers.csv": "awards_players",
    "contrib/HallOfFame.csv": "hall_of_fame",
}


def _list_sabr_folder_files() -> dict[str, int]:
    """Resolve {filename: box_file_id} for every file in the SABR share folder,
    by parsing the Box.postStreamData JSON embedded in the share page HTML."""
    files: dict[str, int] = {}
    page = 1
    page_count = 1
    while page <= page_count:
        resp = requests.get(SABR_SHARE_URL, params={"page": page}, timeout=30)
        resp.raise_for_status()
        match = None
        for script in re.findall(r"<script[^>]*>(.*?)</script>", resp.text, re.S):
            m = re.search(r"Box\.postStreamData = (\{.*\});", script, re.S)
            if m:
                match = m
                break
        if match is None:
            raise RuntimeError(
                "Could not find Box.postStreamData in the SABR share page -- "
                "Box may have changed their page markup. Check "
                f"{SABR_SHARE_URL} in a browser."
            )
        import json
        data = json.loads(match.group(1))
        folder = data.get("/app-api/enduserapp/shared-folder")
        if folder is None:
            raise RuntimeError("SABR share link did not resolve to a folder listing.")
        page_count = folder.get("pageCount", 1)
        for item in folder["items"]:
            if item["type"] == "file":
                files[item["name"]] = item["id"]
        page += 1
    return files


def _download_from_sabr(force: bool) -> bool:
    """Returns True on success, False if the SABR source could not be used
    (caller should fall back to the mirror)."""
    try:
        file_map = _list_sabr_folder_files()
    except (requests.RequestException, RuntimeError) as exc:
        print(f"  SABR folder listing failed: {exc}")
        return False

    missing = [name for name in WANTED_FILES if name not in file_map]
    if missing:
        print(f"  SABR folder is missing expected file(s): {missing}")
        return False

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name in WANTED_FILES:
        dest = RAW_DIR / name
        if dest.exists() and not force:
            continue
        file_id = file_map[name]
        url = f"{SABR_DOWNLOAD_URL}?rm=box_download_shared_file&shared_name={SABR_SHARED_NAME}&file_id=f_{file_id}"
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  failed to download {name}: {exc}")
            return False
        dest.write_bytes(resp.content)
        print(f"  saved {name} ({len(resp.content):,} bytes)")
    return True


def _download_from_fallback_mirror(force: bool) -> bool:
    print("[lahman] falling back to the archived chadwickbureau mirror "
          "(data through 2021 season only)...")
    try:
        resp = requests.get(FALLBACK_SOURCE_URL, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  fallback download failed: {exc}")
        return False

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        root_prefix = names[0].split("/")[0]
        for rel_path in FALLBACK_WANTED_FILES:
            member = f"{root_prefix}/{rel_path}"
            if member not in names:
                print(f"  warning: {rel_path} not found in fallback archive, skipping")
                continue
            dest = RAW_DIR / Path(rel_path).name
            if dest.exists() and not force:
                continue
            with zf.open(member) as src, open(dest, "wb") as out:
                import shutil
                shutil.copyfileobj(src, out)
            print(f"  saved {dest.name} (fallback source)")
    return True


def download(force: bool = False) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    already_cached = all((RAW_DIR / name).exists() for name in WANTED_FILES)
    if already_cached and not force:
        print(f"[lahman] cached CSVs already present in {RAW_DIR}, skipping download "
              f"(use --force to refresh)")
        return

    print(f"[lahman] fetching from SABR Box folder ({SABR_SHARE_URL})...")
    if _download_from_sabr(force):
        print(f"[lahman] done. CSVs cached in {RAW_DIR}")
        return

    if not _download_from_fallback_mirror(force):
        raise RuntimeError("Could not download the Lahman database from SABR or the fallback mirror.")
    print(f"[lahman] done (via fallback mirror). CSVs cached in {RAW_DIR}")


def load_to_duckdb() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        for name, table in WANTED_FILES.items():
            csv_path = RAW_DIR / name
            if not csv_path.exists():
                print(f"  skipping {table}: {name} not downloaded")
                continue
            df = pd.read_csv(csv_path, low_memory=False)

            # Schema differences between the SABR CSVs and the previous
            # source: People.csv carries an extra internal Box row id, and
            # HallOfFame.csv's year column is lowercased.
            if name == "People.csv" and "ID" in df.columns:
                df = df.drop(columns=["ID"])
            if name == "HallOfFame.csv" and "yearid" in df.columns:
                df = df.rename(columns={"yearid": "yearID"})

            con.execute(f"CREATE OR REPLACE TABLE lahman_{table} AS SELECT * FROM df")
            print(f"  loaded lahman_{table} ({len(df):,} rows)")
    finally:
        con.close()
    print(f"[lahman] loaded into {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest the Lahman Baseball Database into DuckDB.")
    parser.add_argument("--force", action="store_true", help="re-download even if cached CSVs exist")
    args = parser.parse_args()

    download(force=args.force)
    load_to_duckdb()


if __name__ == "__main__":
    sys.exit(main())
