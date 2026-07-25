"""Weather Store — the single interface to CHIRPS rainfall data.

Fetches daily CHIRPS-2.0 Africa GeoTIFFs from the official Climate Hazards
Center source on first use, crops them to a country's bounding box, and caches
one compressed array file per country-year. Everything downstream (zoning,
pricing, settlement) reads the cache through `series()` and never knows the
data lives in remote files.

The network fetch is injectable (`fetch_day`) so tests run on synthetic data
with zero downloads.
"""

from __future__ import annotations

import gzip
import json
from calendar import isleap
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

DATASET = "CHIRPS-2.0"
PRODUCT = "africa_daily p05 (0.05 degree)"
BASE_URL = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/africa_daily/tifs/p05"

# Day = one 2D rainfall grid (mm) plus the grid's georeferencing.
Grid = dict  # {"x0","y0","dx","dy","nx","ny"} — x0/y0 are the top-left cell centre


def url_for(day: date) -> str:
    return f"{BASE_URL}/{day.year}/chirps-v2.0.{day.year}.{day.month:02d}.{day.day:02d}.tif.gz"


def download_day(day: date, bbox: tuple) -> tuple[np.ndarray, Grid] | None:
    """Download one CHIRPS day, crop to bbox. None = file not published (404)."""
    import requests
    from rasterio.io import MemoryFile
    from rasterio.windows import from_bounds

    resp = requests.get(url_for(day), timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    payload = resp.content
    if url_for(day).endswith(".gz"):
        payload = gzip.decompress(payload)

    lon_min, lat_min, lon_max, lat_max = bbox
    with MemoryFile(payload) as mem, mem.open() as src:
        window = from_bounds(lon_min, lat_min, lon_max, lat_max, transform=src.transform)
        arr = src.read(1, window=window).astype("float32")
        nodata = src.nodata if src.nodata is not None else -9999.0
        arr[arr == nodata] = np.nan
        t = src.window_transform(window)
        grid: Grid = {
            # cell centres, not corners
            "x0": t.c + t.a / 2, "y0": t.f + t.e / 2,
            "dx": t.a, "dy": t.e,
            "nx": arr.shape[1], "ny": arr.shape[0],
        }
    return arr, grid


def days_in_year(year: int) -> list[date]:
    start = date(year, 1, 1)
    return [start + timedelta(days=i) for i in range(366 if isleap(year) else 365)]


@dataclass
class WeatherStore:
    cache_dir: Path
    fetch_day: Callable[[date, tuple], tuple[np.ndarray, Grid] | None] = field(
        default=staticmethod(download_day)
    )

    def _country_dir(self, country: str) -> Path:
        return Path(self.cache_dir) / "chirps" / country

    def _year_path(self, country: str, year: int) -> Path:
        return self._country_dir(country) / f"{year}.npz"

    def _meta_path(self, country: str, year: int) -> Path:
        return self._country_dir(country) / f"{year}.meta.json"

    def cached_years(self, country: str) -> list[int]:
        d = self._country_dir(country)
        if not d.exists():
            return []
        return sorted(int(p.stem) for p in d.glob("*.npz"))

    def meta(self, country: str, year: int) -> dict:
        return json.loads(self._meta_path(country, year).read_text())

    def ensure_year(
        self,
        country: str,
        year: int,
        bbox: tuple,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Fetch and cache one country-year; instant no-op when already cached."""
        if self._year_path(country, year).exists():
            return self.meta(country, year)

        days = days_in_year(year)
        # Never ask the source for days it cannot have published yet.
        days = [d for d in days if d < date.today()]
        layers: list[np.ndarray] = []
        grid: Grid | None = None
        missing: list[str] = []

        for i, day in enumerate(days):
            result = self.fetch_day(day, bbox)
            if result is None:
                missing.append(day.isoformat())
                layers.append(None)  # type: ignore[arg-type]
            else:
                arr, grid = result
                layers.append(arr)
            if progress:
                progress(i + 1, len(days))

        if grid is None:
            raise RuntimeError(f"No CHIRPS data published for {country} {year}")

        nan_layer = np.full((grid["ny"], grid["nx"]), np.nan, dtype="float32")
        stack = np.stack([nan_layer if a is None else a for a in layers])

        self._country_dir(country).mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self._year_path(country, year), precip=stack)
        meta = {
            "dataset": DATASET,
            "product": PRODUCT,
            "source": BASE_URL,
            "country": country,
            "year": year,
            "bbox": list(bbox),
            "grid": grid,
            "days": len(days),
            "missing_days": missing,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._meta_path(country, year).write_text(json.dumps(meta, indent=2))
        return meta

    def refresh_year(
        self,
        country: str,
        year: int,
        bbox: tuple,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Re-fetch a not-yet-complete year to pick up newly published days.

        Unlike ``ensure_year`` (a permanent no-op once cached), this is the
        settlement data-availability check: a live season's cache is stale the
        moment CHIRPS publishes another day. A year already cached to completion
        (every day of an elapsed year) never changes, so it is left untouched;
        an incomplete year is dropped and re-fetched up to yesterday."""
        path = self._year_path(country, year)
        if path.exists():
            meta = self.meta(country, year)
            if meta.get("days", 0) >= len(days_in_year(year)):
                return meta  # fully elapsed and cached — final, nothing new
            path.unlink()
            self._meta_path(country, year).unlink(missing_ok=True)
        return self.ensure_year(country, year, bbox, progress=progress)

    def pixel_index(self, grid: Grid, lon: float, lat: float) -> tuple[int, int]:
        col = round((lon - grid["x0"]) / grid["dx"])
        row = round((lat - grid["y0"]) / grid["dy"])
        if not (0 <= row < grid["ny"] and 0 <= col < grid["nx"]):
            raise ValueError(f"({lon}, {lat}) is outside the cached window")
        return row, col

    def series(
        self, country: str, lon: float, lat: float, start: date, end: date
    ) -> list[dict]:
        """Daily rainfall at the pixel containing (lon, lat), inclusive of both ends."""
        out: list[dict] = []
        for year in range(start.year, end.year + 1):
            if year not in self.cached_years(country):
                raise FileNotFoundError(f"{country} {year} is not cached — fetch it first")
            meta = self.meta(country, year)
            row, col = self.pixel_index(meta["grid"], lon, lat)
            stack = np.load(self._year_path(country, year))["precip"]
            for i, day in enumerate(days_in_year(year)[: stack.shape[0]]):
                if start <= day <= end:
                    value = float(stack[i, row, col])
                    out.append({"date": day.isoformat(), "mm": None if np.isnan(value) else value})
        return out
