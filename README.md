# CTD Weather Scraping Capstone
# Assignment 9

## Installation

Clone the project and install the requirements:

```bash
pip install selenium pandas
```

Make sure Chromium is installed.

Then run:

```bash
python weather_scraper.py
```

Weather data will be saved as:

```text
weather.csv
```

This project collects weather data and cleans it with Pandas.
# Assignment 10 
## Database portion

The database step uses the existing `weather_raw.csv` file. Our code cleans and transforms the data then updates `weather_clean.csv`. After transformation we perform saves for both stages into `weather.db`.

Run:

```bash
python database.py
```