from pathlib import Path
import re
import sqlite3
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
RAW_CSV = BASE_DIR / "weather_raw.csv"
CLEAN_CSV = BASE_DIR / "weather_clean.csv"
DB_PATH = BASE_DIR / "weather.db"

def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV file: {path.name}")

    return pd.read_csv(path)

def parse_temperature(value: object) -> float | None:
    # raw scrape can contain values like "27 °C", so I pull out the number.
    match = re.search(r"(-?\d+(?:\.\d+)?)", str(value))

    if not match:
        return None

    return float(match.group(1))

def temperature_band(temp_c: float) -> str:
    if temp_c >= 30:
        return "hot"
    if temp_c >= 20:
        return "warm"
    if temp_c >= 10:
        return "mild"
    return "cold"

def clean_raw_data(raw_df: pd.DataFrame) -> pd.DataFrame:
    # keep the original DataFrame untouched so I can show the before/after stages.
    clean_df = raw_df.copy()

    clean_df["city"] = (
        clean_df["city"]
        .fillna("")
        .astype(str)
        .str.replace("*", "", regex=False)
        .str.strip()
    )

    clean_df["condition"] = (
        clean_df["condition"]
        .fillna("Unknown")
        .astype(str)
        .str.strip()
    )

    clean_df["observed_local"] = (
        clean_df["observed_local"]
        .fillna("Unknown")
        .astype(str)
        .str.strip()
    )

    # convert malformed temperature text into a usable numeric column.
    clean_df["temperature_c"] = clean_df["temperature_raw"].map(
        parse_temperature
    )

    clean_df["scraped_at_utc"] = pd.to_datetime(
        clean_df["scraped_at_utc"],
        errors="coerce",
        utc=True,
    )

    #  fields are needed for the database and later analysis.
    clean_df = clean_df.dropna(
        subset=[
            "temperature_c",
            "source_url",
            "scraped_at_utc",
        ]
    )

    clean_df = clean_df[clean_df["city"].ne("")]

    # remove repeated observations before saving the final version.
    clean_df = clean_df.drop_duplicates(
        subset=[
            "city",
            "observed_local",
            "scraped_at_utc",
        ]
    )

    clean_df["temperature_f"] = (
        clean_df["temperature_c"] * 9 / 5 + 32
    ).round(1)

    clean_df["temperature_band"] = clean_df["temperature_c"].map(
        temperature_band
    )

    clean_df["scraped_at_utc"] = (
        clean_df["scraped_at_utc"]
        .dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    )

    return clean_df.sort_values(
        ["temperature_c", "city"],
        ascending=[False, True],
    ).reset_index(drop=True)


def show_cleaning_results(
    raw_df: pd.DataFrame,
    clean_df: pd.DataFrame,
) -> None:
    print("\n--- Before Cleaning ---")
    print(raw_df.head())
    print(f"Rows: {len(raw_df)}")

    print("\n--- After Cleaning ---")
    print(clean_df.head())
    print(f"Rows: {len(clean_df)}")

    # this grouping gives me a quick check of the transformed temperature ranges.
    print("\n--- Temperature Band Summary ---")
    summary = (
        clean_df
        .groupby("temperature_band")["temperature_c"]
        .agg(["count", "mean", "min", "max"])
        .round(1)
    )
    print(summary)

def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP VIEW IF EXISTS temperature_summary;
        DROP TABLE IF EXISTS weather_clean;
        DROP TABLE IF EXISTS weather_raw;

        CREATE TABLE weather_raw (
            id INTEGER PRIMARY KEY,
            city TEXT,
            observed_local TEXT,
            condition TEXT,
            temperature_raw TEXT,
            source_url TEXT,
            scraped_at_utc TEXT
        );

        CREATE TABLE weather_clean (
            id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            observed_local TEXT NOT NULL,
            condition TEXT NOT NULL,
            temperature_raw TEXT NOT NULL,
            source_url TEXT NOT NULL,
            scraped_at_utc TEXT NOT NULL,
            temperature_c REAL NOT NULL,
            temperature_f REAL NOT NULL,
            temperature_band TEXT NOT NULL
                CHECK (
                    temperature_band IN ('hot', 'warm', 'mild', 'cold')
                ),
            UNIQUE(city, observed_local, scraped_at_utc)
        );

        CREATE INDEX idx_weather_clean_city
        ON weather_clean(city);

        CREATE INDEX idx_weather_clean_band
        ON weather_clean(temperature_band);

        CREATE VIEW temperature_summary AS
        SELECT
            temperature_band,
            COUNT(*) AS observation_count,
            ROUND(AVG(temperature_c), 1) AS average_temperature_c,
            MIN(temperature_c) AS minimum_temperature_c,
            MAX(temperature_c) AS maximum_temperature_c
        FROM weather_clean
        GROUP BY temperature_band;
        """
    )


def insert_raw(
    conn: sqlite3.Connection,
    raw_df: pd.DataFrame,
) -> None:
    columns = [
        "city",
        "observed_local",
        "condition",
        "temperature_raw",
        "source_url",
        "scraped_at_utc",
    ]

    rows = (
        raw_df[columns]
        .where(pd.notna(raw_df[columns]), None)
        .itertuples(index=False, name=None)
    )

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
        rows,
    )

def insert_clean(
    conn: sqlite3.Connection,
    clean_df: pd.DataFrame,
) -> None:
    columns = [
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

    rows = clean_df[columns].itertuples(index=False, name=None)

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
        rows,
    )

def save_database(
    raw_df: pd.DataFrame,
    clean_df: pd.DataFrame,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        create_schema(conn)
        insert_raw(conn, raw_df)
        insert_clean(conn, clean_df)

        raw_count = conn.execute(
            "SELECT COUNT(*) FROM weather_raw"
        ).fetchone()[0]

        clean_count = conn.execute(
            "SELECT COUNT(*) FROM weather_clean"
        ).fetchone()[0]

        summary = conn.execute(
            """
            SELECT
                temperature_band,
                observation_count,
                average_temperature_c
            FROM temperature_summary
            ORDER BY average_temperature_c DESC
            """
        ).fetchall()

    print(f"\nSaved SQLite database: {DB_PATH.name}")
    print(f"weather_raw rows: {raw_count}")
    print(f"weather_clean rows: {clean_count}")

    print("\n--- SQLite Summary View ---")
    for band, count, average in summary:
        print(band, count, average)

def main() -> None:
    # start from the raw CSV, then rebuild the clean CSV from the same steps.
    raw_df = load_csv(RAW_CSV)
    clean_df = clean_raw_data(raw_df)

    show_cleaning_results(raw_df, clean_df)

    # this keeps the cleaned CSV and database table based on the same DataFrame.
    clean_df.to_csv(CLEAN_CSV, index=False)

    # each CSV stage now has its own SQLite table.
    clean_csv_df = load_csv(CLEAN_CSV)
    save_database(raw_df, clean_csv_df)

if __name__ == "__main__":
    main()
