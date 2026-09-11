"""Selenium scraper, cleaning pipeline, and SQLite export for the weather capstone."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = BASE_DIR / "db"
RAW_PATH = DATA_DIR / "weather_raw.csv"
CLEAN_PATH = DATA_DIR / "weather_clean.csv"
DB_PATH = DB_DIR / "weather.db"
WEATHER_URL = "https://www.timeanddate.com/weather/"
USER_AGENT = "weather-scraping-capstone/1.0"


@dataclass(frozen=True, slots=True)
class WeatherRecord:
    city: str
    observed_local: str
    condition: str
    temperature_raw: str
    source_url: str
    scraped_at_utc: str


def text_of(element, fallback: str = "Unknown") -> str:
    """Normalize whitespace from a Selenium element and provide a fallback."""
    value = " ".join(element.text.split())
    return value or fallback


def create_driver() -> webdriver.Chrome:
    """Create a headless Chrome driver with a project-specific user agent."""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-first-run")
    options.add_argument(f"--user-agent={USER_AGENT}")
    options.page_load_strategy = "eager"
    return webdriver.Chrome(options=options)


def scrape() -> list[WeatherRecord]:
    """Collect one weather observation per unique city/local-time pair."""
    driver = create_driver()
    try:
        driver.get(WEATHER_URL)
        table = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#wt-tb, table.zebra, table"))
        )
        scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        records: list[WeatherRecord] = []
        seen: set[tuple[str, str]] = set()

        for row in table.find_elements(By.CSS_SELECTOR, "tr"):
            cells = row.find_elements(By.CSS_SELECTOR, "td")
            # The source page presents repeating groups of city, time, condition, temperature.
            for start in range(0, len(cells) - 3, 4):
                city_cell, time_cell, condition_cell, temperature_cell = cells[start : start + 4]
                city = text_of(city_cell, "")
                observed = text_of(time_cell)
                condition = text_of(condition_cell)
                temperature = text_of(temperature_cell, "")

                # Missing core fields and repeated observations are skipped.
                if not city or not temperature or (city, observed) in seen:
                    continue
                seen.add((city, observed))

                links = city_cell.find_elements(By.CSS_SELECTOR, "a[href]")
                source_url = links[0].get_attribute("href") if links else WEATHER_URL
                records.append(
                    WeatherRecord(
                        city=city,
                        observed_local=observed,
                        condition=condition,
                        temperature_raw=temperature,
                        source_url=source_url or WEATHER_URL,
                        scraped_at_utc=scraped_at,
                    )
                )
        return records
    finally:
        driver.quit()


def temperature_c(value: object) -> float | None:
    """Convert scraped C/F temperature text to numeric Celsius."""
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?", str(value), re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    if (match.group(2) or "C").upper() == "F":
        number = (number - 32) * 5 / 9
    return round(number, 1)


def temperature_band(value: float) -> str:
    """Group temperatures into simple bands used by the dashboard filters."""
    if value < 20:
        return "mild"
    if value < 30:
        return "warm"
    return "hot"


def raw_frame(records: list[WeatherRecord]) -> pd.DataFrame:
    """Create the uncleaned DataFrame used for the before-cleaning CSV."""
    return pd.DataFrame(asdict(record) for record in records)


def clean(frame: pd.DataFrame) -> pd.DataFrame:
    """Clean raw observations and add numeric/unit and grouping features."""
    if frame.empty:
        raise RuntimeError("The scraper returned no records.")

    cleaned = frame.copy()
    cleaned["city"] = cleaned["city"].str.replace("*", "", regex=False).str.strip()
    cleaned["condition"] = cleaned["condition"].fillna("Unknown").str.strip()
    cleaned["observed_local"] = cleaned["observed_local"].fillna("Unknown").str.strip()
    cleaned["temperature_c"] = cleaned["temperature_raw"].map(temperature_c)

    cleaned = cleaned.dropna(subset=["city", "temperature_c"])
    cleaned = cleaned[cleaned["city"].ne("")]
    cleaned = cleaned.drop_duplicates(subset=["city", "observed_local", "temperature_c"])

    cleaned["temperature_f"] = (cleaned["temperature_c"] * 9 / 5 + 32).round(1)
    cleaned["temperature_band"] = cleaned["temperature_c"].map(temperature_band)

    return cleaned.sort_values(
        ["temperature_c", "city"], ascending=[False, True]
    ).reset_index(drop=True)


def save_outputs(raw: pd.DataFrame, cleaned: pd.DataFrame) -> None:
    """Persist before/after CSV files and the cleaned SQLite table."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    raw.to_csv(RAW_PATH, index=False)
    cleaned.to_csv(CLEAN_PATH, index=False)
    with sqlite3.connect(DB_PATH) as connection:
        cleaned.to_sql("weather_observations", connection, if_exists="replace", index=False)


def main() -> None:
    records = scrape()
    before = raw_frame(records)
    after = clean(before)
    save_outputs(before, after)
    print(f"Raw rows: {len(before)}")
    print(f"Clean rows: {len(after)}")
    print(f"Saved raw CSV: {RAW_PATH}")
    print(f"Saved cleaned CSV: {CLEAN_PATH}")
    print(f"Saved SQLite database: {DB_PATH}")


if __name__ == "__main__":
    main()
