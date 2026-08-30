from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Weather site I am using for weather data
WEATHER_URL = "https://www.timeanddate.com/weather/"

# Give my scraper a user agent name
USER_AGENT = "weather-scraping-capstone/1.0"

# Save the finished CSV into the same folder as the python file
OUTPUT_PATH = Path(__file__).resolve().parent / "weather.csv"

@dataclass(frozen=True, slots=True)
class WeatherRecord:
    # I made one structure for each weather result so the data stays
    # organized before I turn everything into a pandas DataFrame.
    city: str
    observed_local: str
    condition: str
    temperature_raw: str
    source_url: str
    scraped_at_utc: str

def text_of(element, fallback: str = "Unknown") -> str:
    # Selenium sometimes gives text back with extra spaces or line breaks,
    # so I clean it up here instead of repeating this everywhere.
    value = " ".join(element.text.split())
    return value or fallback

def condition_of(cell) -> str:
    # I only use the text Selenium finds in the condition cell.
    return text_of(cell)

def create_driver() -> webdriver.Chrome:
    # These are the Chrome settings I am using for the scraper.
    options = webdriver.ChromeOptions()

    # Headless mode lets the browser run without opening a visible window.
    options.add_argument("--headless=new")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-first-run")
    options.add_argument(f"--user-agent={USER_AGENT}")

    # I do not need to wait for every resource on the page
    # before I start looking for the weather table.
    options.page_load_strategy = "eager"

    return webdriver.Chrome(options=options)

def scrape() -> list[WeatherRecord]:
    driver = create_driver()

    try:
        driver.get(WEATHER_URL)

        # Instead of using time.sleep(), I wait until Selenium can
        # actually find the weather table.
        table = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#wt-tb, table.zebra, table")
            )
        )

        # Weather changes constantly, so I also save when I scraped it.
        scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        records: list[WeatherRecord] = []

        # I use this to avoid saving the same city and observation twice.
        seen: set[tuple[str, str]] = set()

        for row in table.find_elements(By.CSS_SELECTOR, "tr"):
            cells = row.find_elements(By.CSS_SELECTOR, "td")

            # The page puts the data into groups of four:
            # city, time, condition, and temperature.
            for start in range(0, len(cells) - 3, 4):
                city_cell, time_cell, condition_cell, temperature_cell = (
                    cells[start : start + 4]
                )

                city = text_of(city_cell, "")
                observed = text_of(time_cell)
                condition = condition_of(condition_cell)
                temperature = text_of(temperature_cell, "")

                # If the row is missing the important stuff or I already
                # collected it, I just skip it.
                if not city or not temperature or (city, observed) in seen:
                    continue

                seen.add((city, observed))

                # I also save the city's link when the page gives me one.
                links = city_cell.find_elements(By.CSS_SELECTOR, "a[href]")

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
        # Close Chrome if something goes wrong
        driver.quit()

def temperature_c(value: object) -> float | None:
    # The scraped temperature is still text at this point.
    # This regex pulls the actual number and the C/F unit out of it.
    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?",
        str(value),
        re.IGNORECASE,
    )

    if not match:
        return None

    number = float(match.group(1))

    # I picked Celsius as the main value for cleaning the data,
    # so Fahrenheit values get converted here.
    if (match.group(2) or "C").upper() == "F":
        number = (number - 32) * 5 / 9

    return round(number, 1)

def clean(records: list[WeatherRecord]) -> pd.DataFrame:
    if not records:
        raise RuntimeError("the scraper returned no records")

    # Now that scraping is finished I can convert the records into pandas.
    frame = pd.DataFrame(asdict(record) for record in records)

    # A few city names can have an asterisk from the website,
    # so I remove that before saving the final data.
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

    # Keeping the original temperature but also making a numeric Celsius column
    frame["temperature_c"] = frame["temperature_raw"].map(temperature_c)

    # Rows without a usable city or temperature are not useful to me.
    frame = frame.dropna(subset=["city", "temperature_c"])
    frame = frame[frame["city"].ne("")]

    # One more duplicate check after the cleaning step.
    frame = frame.drop_duplicates(
        subset=["city", "observed_local", "temperature_c"]
    )

    # I decided to keep Fahrenheit too since that will probably be
    # easier to read for some people later.
    frame["temperature_f"] = (
        frame["temperature_c"] * 9 / 5 + 32
    ).round(1)

    # I am just sorting the finished data from hottest to coolest. As I think i want
    # to do a hot/cool visualization with streamlit later in the project
    return frame.sort_values(
        ["temperature_c", "city"],
        ascending=[False, True],
    ).reset_index(drop=True)

def save_csv(frame: pd.DataFrame) -> None:
    # Save the cleaned data into a CSV file
    frame.to_csv(OUTPUT_PATH, index=False)

def main() -> None:
    # The scraper's entry point is this main() function,
    frame = clean(records)
    save_csv(frame)

    print(
        f"Scraped {len(records)} rows. "
        f"Saved {len(frame)} clean rows to {OUTPUT_PATH.name}"
    )

if __name__ == "__main__":
    main()
