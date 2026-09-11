from pathlib import Path
import sqlite3
import pandas as pd
import plotly.express as px
import streamlit as st

# path to the SQLite database used for the dashboard
DB_PATH = Path(__file__).resolve().parent / "db" / "weather.db"

# keep the temperature bands in the same order across each chart
BAND_ORDER = ["mild", "warm", "hot"]

# colors used for each temperature band
BAND_COLORS = {
    "mild": "#0d6fc8",
    "warm": "#76bdf2",
    "hot": "#ff4b4b",
}


@st.cache_data
def load_weather() -> pd.DataFrame:
    # load cleaned weather observations from the capstone SQLite database
    with sqlite3.connect(DB_PATH) as connection:
        frame = pd.read_sql_query(
            "SELECT * FROM weather_observations ORDER BY temperature_c DESC",
            connection,
        )

    # return the dataframe so it can be reused throughout the dashboard
    return frame


def unit_settings(unit_name: str) -> tuple[str, str, str]:
    # choose which temperature column and labels to use based on the selected unit
    if unit_name == "Fahrenheit":
        return "temperature_f", "°F", "Temperature (°F)"

    # use Celsius by default
    return "temperature_c", "°C", "Temperature (°C)"


# configure the Streamlit page before displaying dashboard content
st.set_page_config(page_title="Global Weather Explorer", layout="wide")

# load the weather data once for the dashboard
weather = load_weather()

st.title("Global Weather Explorer")
st.caption(
    "Explore cleaned global weather observations. Every metric and visualization "
    "responds to the controls in the sidebar."
)

# sidebar controls let the user change what data is shown
st.sidebar.header("Explore the data")

# allow the user to switch between Celsius and Fahrenheit
unit_name = st.sidebar.radio(
    "Display unit",
    ["Celsius", "Fahrenheit"],
    horizontal=True,
)

# get the correct column and labels for the selected unit
temperature_column, unit_symbol, temperature_label = unit_settings(unit_name)

# only show temperature bands that actually exist in the dataset
available_bands = [
    band for band in BAND_ORDER if band in set(weather["temperature_band"].dropna())
]

# let the user select which temperature bands should be included
selected_bands = st.sidebar.multiselect(
    "Temperature bands",
    available_bands,
    default=available_bands,
)

# find the full temperature range for the selected unit
minimum = float(weather[temperature_column].min())
maximum = float(weather[temperature_column].max())

# let the user narrow down the temperature range
selected_range = st.sidebar.slider(
    f"Temperature range ({unit_symbol})",
    minimum,
    maximum,
    (minimum, maximum),
    step=0.5,
)

# apply both sidebar filters to the weather data
filtered = weather[
    weather["temperature_band"].isin(selected_bands)
    & weather[temperature_column].between(*selected_range)
].copy()

# stop here if the filters remove every observation
if filtered.empty:
    st.warning("No observations match the current filters. Broaden the selection to continue.")
    st.stop()

# show a quick summary of the currently filtered data
metric_a, metric_b, metric_c = st.columns(3)

metric_a.metric("Cities shown", len(filtered))

metric_b.metric(
    "Average temperature",
    f"{filtered[temperature_column].mean():.1f} {unit_symbol}",
)

metric_c.metric(
    "Temperature span",
    f"{filtered[temperature_column].min():.1f}–{filtered[temperature_column].max():.1f} {unit_symbol}",
)

# bar chart for comparing temperatures between cities
st.subheader("Temperature by city")
st.caption("Compare the current observations from coolest to warmest.")

# sort the data so the cities appear from coolest to warmest
city_chart = px.bar(
    filtered.sort_values(temperature_column),
    x=temperature_column,
    y="city",
    color="temperature_band",
    orientation="h",
    labels={
        temperature_column: temperature_label,
        "city": "City",
        "temperature_band": "Temperature band",
    },
    hover_data={
        temperature_column: ":.1f",
        "temperature_band": True,
        "observed_local": True,
        "condition": True,
    },
    category_orders={"temperature_band": BAND_ORDER},
    color_discrete_map=BAND_COLORS,
)

# keep the legend label simple and consistent
city_chart.update_layout(legend_title_text="Temperature band")
st.plotly_chart(city_chart, width="stretch")

# split the next two charts into side-by-side columns
left, right = st.columns(2)

# count how many cities fall into each temperature band
band_counts = (
    filtered["temperature_band"]
    .value_counts()
    .reindex(BAND_ORDER, fill_value=0)
    .rename_axis("temperature_band")
    .reset_index(name="cities")
)

# remove empty bands so they do not appear in the pie chart
band_counts = band_counts[band_counts["cities"] > 0]

with left:
    # show the percentage of cities inside each temperature band
    st.subheader("Temperature band distribution")
    st.caption("See how the filtered cities are divided among mild, warm, and hot observations.")

    band_chart = px.pie(
        band_counts,
        names="temperature_band",
        values="cities",
        hole=0.45,
        category_orders={"temperature_band": BAND_ORDER},
        color="temperature_band",
        color_discrete_map=BAND_COLORS,
    )

    # place the percentage and band name directly inside each section
    band_chart.update_traces(textposition="inside", textinfo="percent+label")
    band_chart.update_layout(showlegend=False)

    st.plotly_chart(band_chart, width="stretch")

with right:
    # compare the temperature spread inside each band
    st.subheader("Temperature spread by band")
    st.caption("Compare the median, range, and individual city observations within each temperature band.")

    band_chart = px.pie(
        band_counts,
        names="temperature_band",
        values="cities",
        hole=0.45,
        category_orders={"temperature_band": BAND_ORDER},
        color="temperature_band",
        color_discrete_map=BAND_COLORS,
    )

    # show the individual city points along with the box plot
    spread_chart = px.box(
        filtered,
        x="temperature_band",
        y=temperature_column,
        color="temperature_band",
        points="all",
        hover_name="city",
        labels={
            temperature_column: temperature_label,
            "temperature_band": "Temperature band",
        },
        category_orders={"temperature_band": BAND_ORDER},
        color_discrete_map=BAND_COLORS,
    )

    # the colors already identify each band, so the legend is not needed
    spread_chart.update_layout(showlegend=False)
    st.plotly_chart(spread_chart, width="stretch")

# give the user access to the actual rows behind the charts
with st.expander("View filtered observations"):
    # only keep the columns that are useful for the dashboard table
    table = filtered[
        ["city", "condition", temperature_column, "temperature_band", "observed_local"]
    ].copy()

    # rename the database column names so they look cleaner in the interface
    table = table.rename(
        columns={
            "city": "City",
            "condition": "Condition",
            temperature_column: temperature_label,
            "temperature_band": "Temperature band",
            "observed_local": "Observed local",
        }
    )

    st.dataframe(table, width="stretch", hide_index=True)

# show when the source data was originally scraped
st.caption(f"Source observations scraped at {filtered['scraped_at_utc'].iloc[0]}.")