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


# Main project paths.
WEATHER_URL = "https://www.timeanddate.com/weather/"
USER_AGENT = "weather-scraping-capstone/1.0"

BASE_DIR = Path(__file__).resolve().parent

RAW_CSV = BASE_DIR / "weather_raw.csv"
CLEAN_CSV = BASE_DIR / "weather_clean.csv"
DB_PATH = BASE_DIR / "weather.db"


@dataclass(frozen=True, slots=True)
class WeatherRecord:
    # Keep each scraped result in the same shape before Pandas.
    city: str
    observed_local: str
    condition: str
    temperature_raw: str
    source_url: str
    scraped_at_utc: str


def text_of(element, fallback: str = "Unknown") -> str:
    # Clean up extra spaces Selenium sometimes gives me.
    value = " ".join(element.text.split())
    return value or fallback


def condition_of(cell) -> str:
    return text_of(cell)


def create_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()

    # Run Chromium without opening a browser window.
    options.add_argument("--headless=new")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-first-run")
    options.add_argument(f"--user-agent={USER_AGENT}")

    options.page_load_strategy = "eager"

    return webdriver.Chrome(options=options)


def scrape() -> list[WeatherRecord]:
    driver = create_driver()

    try:
        driver.get(WEATHER_URL)

        # Wait for the weather table instead of using sleep().
        table = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#wt-tb, table.zebra, table")
            )
        )

        scraped_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )

        records: list[WeatherRecord] = []
        seen: set[tuple[str, str]] = set()

        for row in table.find_elements(By.CSS_SELECTOR, "tr"):
            cells = row.find_elements(By.CSS_SELECTOR, "td")

            # The table data comes in groups of four cells.
            for start in range(0, len(cells) - 3, 4):
                (
                    city_cell,
                    time_cell,
                    condition_cell,
                    temperature_cell,
                ) = cells[start:start + 4]

                city = text_of(city_cell, "")
                observed = text_of(time_cell)
                condition = condition_of(condition_cell)
                temperature = text_of(temperature_cell, "")

                # Skip bad rows and anything I already collected.
                if (
                    not city
                    or not temperature
                    or (city, observed) in seen
                ):
                    continue

                seen.add((city, observed))

                links = city_cell.find_elements(
                    By.CSS_SELECTOR,
                    "a[href]",
                )

                source_url = (
                    links[0].get_attribute("href")
                    if links
                    else WEATHER_URL
                )

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
        # Make sure Chrome closes even if the scrape fails.
        driver.quit()


def temperature_c(value: object) -> float | None:
    # Pull the temperature number and unit out of the scraped text.
    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?",
        str(value),
        re.IGNORECASE,
    )

    if not match:
        return None

    number = float(match.group(1))
    unit = (match.group(2) or "C").upper()

    if unit == "F":
        number = (number - 32) * 5 / 9

    return round(number, 1)


def temperature_band(temp_c: float) -> str:
    # These groups should be useful for the dashboard later.
    if temp_c >= 30:
        return "hot"

    if temp_c >= 20:
        return "warm"

    if temp_c >= 10:
        return "mild"

    return "cold"


def clean(raw_frame: pd.DataFrame) -> pd.DataFrame:
    # Leave the original scrape untouched.
    frame = raw_frame.copy()

    # Timeanddate sometimes puts an asterisk beside city names.
    frame["city"] = (
        frame["city"]
        .str.replace("*", "", regex=False)
        .str.strip()
    )

    frame["condition"] = (
        frame["condition"]
        .fillna("Unknown")
        .str.strip()
    )

    frame["observed_local"] = (
        frame["observed_local"]
        .fillna("Unknown")
        .str.strip()
    )

    # Turn the temperature text into something I can calculate with.
    frame["temperature_c"] = (
        frame["temperature_raw"]
        .map(temperature_c)
    )

    frame = frame.dropna(
        subset=[
            "city",
            "temperature_c",
            "source_url",
            "scraped_at_utc",
        ]
    )

    frame = frame[frame["city"].ne("")]

    # One more duplicate check after cleaning.
    frame = frame.drop_duplicates(
        subset=[
            "city",
            "observed_local",
            "scraped_at_utc",
        ]
    )

    frame["temperature_f"] = (
        frame["temperature_c"] * 9 / 5 + 32
    ).round(1)

    frame["temperature_band"] = (
        frame["temperature_c"]
        .map(temperature_band)
    )

    # Hottest cities first makes the output easier to check.
    return frame.sort_values(
        ["temperature_c", "city"],
        ascending=[False, True],
    ).reset_index(drop=True)


def save_csvs(
    raw_frame: pd.DataFrame,
    clean_frame: pd.DataFrame,
) -> None:
    # Keep both stages so I can compare them later.
    raw_frame.to_csv(RAW_CSV, index=False)
    clean_frame.to_csv(CLEAN_CSV, index=False)


def save_database(
    raw_frame: pd.DataFrame,
    clean_frame: pd.DataFrame,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        # Keep the original scrape separate from the transformed data.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_raw (
                id INTEGER PRIMARY KEY,
                city TEXT NOT NULL,
                observed_local TEXT,
                condition TEXT,
                temperature_raw TEXT,
                source_url TEXT NOT NULL,
                scraped_at_utc TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_clean (
                id INTEGER PRIMARY KEY,
                city TEXT NOT NULL,
                observed_local TEXT,
                condition TEXT,
                temperature_raw TEXT,
                source_url TEXT NOT NULL,
                scraped_at_utc TEXT NOT NULL,
                temperature_c REAL NOT NULL,
                temperature_f REAL NOT NULL,
                temperature_band TEXT NOT NULL
            )
            """
        )

        # I only want the latest scrape in these tables for now.
        conn.execute("DELETE FROM weather_raw")
        conn.execute("DELETE FROM weather_clean")

        raw_rows = raw_frame[
            [
                "city",
                "observed_local",
                "condition",
                "temperature_raw",
                "source_url",
                "scraped_at_utc",
            ]
        ].itertuples(index=False, name=None)

        conn.executemany(
            """
            INSERT INTO weather_raw (
                city,
                observed_local,
                condition,
                temperature_raw,
                source_url,
                scraped_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            raw_rows,
        )

        clean_rows = clean_frame[
            [
                "city",
                "observed_local",
                "condition",
                "temperature_raw",
                "source_url",
                "scraped_at_utc",
                "temperature_c",
                "temperature_f",
                "temperature_band",
            ]
        ].itertuples(index=False, name=None)

        conn.executemany(
            """
            INSERT INTO weather_clean (
                city,
                observed_local,
                condition,
                temperature_raw,
                source_url,
                scraped_at_utc,
                temperature_c,
                temperature_f,
                temperature_band
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            clean_rows,
        )

        # These are fields I will probably query and filter by later.
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_weather_clean_city
            ON weather_clean(city)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_weather_clean_band
            ON weather_clean(temperature_band)
            """
        )

        # Quick check that both tables actually got populated.
        raw_count = conn.execute(
            "SELECT COUNT(*) FROM weather_raw"
        ).fetchone()[0]

        clean_count = conn.execute(
            "SELECT COUNT(*) FROM weather_clean"
        ).fetchone()[0]

    print(
        f"Database saved: {DB_PATH.name} "
        f"({raw_count} raw rows, {clean_count} clean rows)"
    )


def show_cleaning_results(
    raw_frame: pd.DataFrame,
    clean_frame: pd.DataFrame,
) -> None:
    # Show the before/after part of the cleaning rubric.
    print("\n--- Before Cleaning ---")
    print(raw_frame.head())
    print(f"Rows: {len(raw_frame)}")

    print("\n--- After Cleaning ---")
    print(clean_frame.head())
    print(f"Rows: {len(clean_frame)}")

    print("\n--- Temperature Summary ---")

    summary = (
        clean_frame
        .groupby("temperature_band")["temperature_c"]
        .agg(["count", "mean", "min", "max"])
        .round(1)
    )

    print(summary)


def main() -> None:
    records = scrape()

    if not records:
        raise RuntimeError("the scraper returned no records")

    # Save the raw stage before doing any cleaning.
    raw_frame = pd.DataFrame(
        asdict(record)
        for record in records
    )

    clean_frame = clean(raw_frame)

    show_cleaning_results(
        raw_frame,
        clean_frame,
    )

    save_csvs(
        raw_frame,
        clean_frame,
    )

    # The same two stages also go into SQLite.
    save_database(
        raw_frame,
        clean_frame,
    )

    print(
        f"\nScraped {len(raw_frame)} rows. "
        f"Saved {len(clean_frame)} clean rows."
    )


if __name__ == "__main__":
    main()