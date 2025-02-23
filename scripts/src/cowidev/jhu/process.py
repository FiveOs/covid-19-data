import pandas as pd
import numpy as np
import os
from datetime import datetime

from cowidev.megafile.steps.test import get_testing
from cowidev.jhu.load import (
    load_population,
    load_eu_country_names,
    LOCATIONS_BY_CONTINENT,
    LOCATIONS_BY_WB_INCOME_GROUP,
)


ZERO_DAY = "2020-01-21"
zero_day = datetime.strptime(ZERO_DAY, "%Y-%m-%d")

LARGE_DATA_CORRECTIONS = [
    ("Austria", "2022-04-21", "deaths"),
    ("Austria", "2022-04-22", "deaths"),
    ("Brazil", "2021-09-18", "cases"),
    ("Chile", "2020-07-17", "deaths"),
    ("Chile", "2022-03-21", "deaths"),
    ("China", "2020-04-17", "deaths"),
    ("Denmark", "2021-12-21", "deaths"),
    ("Ecuador", "2020-09-07", "deaths"),
    ("Ecuador", "2021-07-20", "deaths"),
    ("Finland", "2022-03-07", "deaths"),
    ("Iceland", "2022-05-17", "deaths"),
    ("India", "2021-06-10", "deaths"),
    ("Mexico", "2020-10-05", "deaths"),
    ("Mexico", "2021-06-01", "deaths"),
    ("Moldova", "2021-12-31", "deaths"),
    ("Norway", "2022-03-17", "deaths"),
    ("Oman", "2022-06-16", "deaths"),
    ("South Africa", "2021-11-23", "cases"),
    ("South Africa", "2022-01-06", "deaths"),
    ("Spain", "2020-06-19", "deaths"),
    ("Turkey", "2020-12-10", "cases"),
    ("United Kingdom", "2022-01-31", "cases"),
    ("United Kingdom", "2022-02-01", "deaths"),
    ("United Kingdom", "2022-04-06", "deaths"),
]

# ==============
# Data injection
# ==============
def standardize_data(df):
    df = (
        df[["date", "location", "new_cases", "new_deaths", "total_cases", "total_deaths"]]
        .pipe(discard_rows)
        .pipe(inject_owid_aggregates)
        .pipe(inject_weekly_growth)
        .pipe(inject_biweekly_growth)
        .pipe(inject_doubling_days)
        .pipe(
            inject_per_million,
            [
                "new_cases",
                "new_deaths",
                "total_cases",
                "total_deaths",
                "weekly_cases",
                "weekly_deaths",
                "biweekly_cases",
                "biweekly_deaths",
            ],
        )
        .pipe(inject_rolling_avg)
        .pipe(inject_cfr)
        .pipe(inject_days_since)
        .pipe(inject_exemplars)
        .sort_values(by=["location", "date"])
    )
    return df


# Useful for adding it to regions.csv and
def inject_population(df):
    return df.merge(load_population(), how="left", on="location")


def drop_population(df):
    return df.drop(columns=["population_year", "population"])


def inject_per_million(df, measures):
    df = inject_population(df)
    for measure in measures:
        pop_measure = measure + "_per_million"
        series = df[measure] / (df["population"] / 1e6)
        df[pop_measure] = series.round(decimals=3)
    return drop_population(df)


# ===================================
# OWID continents + custom aggregates
# ===================================

aggregates_spec = {
    "World": {"include": None, "exclude": None},
    "World excl. China": {"exclude": ["China"]},
    "World excl. China and South Korea": {"exclude": ["China", "South Korea"]},
    "World excl. China, South Korea, Japan and Singapore": {"exclude": ["China", "South Korea", "Japan", "Singapore"]},
    # European Union
    "European Union": {"include": load_eu_country_names()},
    # OWID continents
    **{continent: {"include": locations, "exclude": None} for continent, locations in LOCATIONS_BY_CONTINENT.items()},
    # Asia without China
    "Asia excl. China": {"include": list(set(LOCATIONS_BY_CONTINENT["Asia"]) - set(["China"]))},
    # World Bank income groups
    **{
        income_group: {"include": locations, "exclude": None}
        for income_group, locations in LOCATIONS_BY_WB_INCOME_GROUP.items()
    },
}


def _sum_aggregate(df, name, include=None, exclude=None):
    df = df.copy()
    if include:
        df = df[df["location"].isin(include)]
    if exclude:
        df = df[~df["location"].isin(exclude)]
    df = df.groupby("date").sum().reset_index()
    df["location"] = name
    return df


def inject_owid_aggregates(df):
    return pd.concat(
        [
            df,
            *[_sum_aggregate(df, name, **params) for name, params in aggregates_spec.items()],
        ],
        sort=True,
        ignore_index=True,
    )


# =======================
# Total/daily calculation
# =======================


def inject_total_daily_cols(df, measures):
    # must sort in order to have the cumsum() and diff() in the right direction
    df = df.copy().sort_values(by=["location", "date"])
    for measure in measures:
        total_col = "total_%s" % measure
        daily_col = "new_%s" % measure
        if total_col not in df.columns and daily_col in df.columns:
            df[total_col] = df.groupby("location")[daily_col].cumsum().astype("Int64")
        elif daily_col not in df.columns and total_col in df.columns:
            df[daily_col] = df.groupby("location")[total_col].diff().astype("Int64")
    return df


# ======================
# 'Days since' variables
# ======================

days_since_spec = {
    "days_since_100_total_cases": {
        "value_col": "total_cases",
        "value_threshold": 100,
        "positive_only": False,
    },
    "days_since_5_total_deaths": {
        "value_col": "total_deaths",
        "value_threshold": 5,
        "positive_only": False,
    },
    "days_since_1_total_cases_per_million": {
        "value_col": "total_cases_per_million",
        "value_threshold": 1,
        "positive_only": False,
    },
    "days_since_0_1_total_deaths_per_million": {
        "value_col": "total_deaths_per_million",
        "value_threshold": 0.1,
        "positive_only": False,
    },
}


def _get_date_of_threshold(df, col, threshold):
    try:
        return df["date"][df[col] >= threshold].iloc[0]
    except:
        return None


def _date_diff(a, b, positive_only=False):
    if pd.isnull(a) or pd.isnull(b):
        return None
    diff = (a - b).days
    if positive_only and diff < 0:
        return None
    return diff


def _days_since(df, spec):
    ref_date = pd.to_datetime(_get_date_of_threshold(df, spec["value_col"], spec["value_threshold"]))
    return (
        pd.to_datetime(df["date"]).map(lambda date: _date_diff(date, ref_date, spec["positive_only"])).astype("Int64")
    )


def inject_days_since(df):
    df = df.copy()
    for col, spec in days_since_spec.items():
        df[col] = (
            df[["date", "location", spec["value_col"]]]
            .groupby("location")
            .apply(lambda df_group: _days_since(df_group, spec))
            .reset_index(level=0, drop=True)
        )
    return df


# ===================
# Case Fatality Ratio
# ===================


def _apply_row_cfr_100(row):
    if pd.notnull(row["total_cases"]) and row["total_cases"] >= 100:
        return row["cfr"]
    return pd.NA


def inject_cfr(df):
    cfr_series = (df["total_deaths"] / df["total_cases"]) * 100
    df["cfr"] = cfr_series.round(decimals=3)
    df["cfr_100_cases"] = df.apply(_apply_row_cfr_100, axis=1)
    return df


# ================
# Rolling averages
# ================

rolling_avg_spec = {
    "new_cases_7_day_avg_right": {
        "col": "new_cases",
        "window": 7,
        "min_periods": 6,
        "center": False,
    },
    "new_deaths_7_day_avg_right": {
        "col": "new_deaths",
        "window": 7,
        "min_periods": 6,
        "center": False,
    },
    "new_cases_per_million_7_day_avg_right": {
        "col": "new_cases_per_million",
        "window": 7,
        "min_periods": 6,
        "center": False,
    },
    "new_deaths_per_million_7_day_avg_right": {
        "col": "new_deaths_per_million",
        "window": 7,
        "min_periods": 6,
        "center": False,
    },
}


def inject_rolling_avg(df):
    df = df.copy().sort_values(by="date")
    for col, spec in rolling_avg_spec.items():
        df[col] = df[spec["col"]].astype("float")
        df[col] = (
            df.groupby("location")[col]
            .rolling(
                window=spec["window"],
                min_periods=spec["min_periods"],
                center=spec["center"],
            )
            .mean()
            .round(decimals=3)
            .reset_index(level=0, drop=True)
        )
    return df


# ===========================
# Variables to find exemplars
# ===========================


def inject_exemplars(df):
    df = inject_population(df)

    # Inject days since 100th case IF population ≥ 5M
    def mapper_days_since(row):
        if pd.notnull(row["population"]) and row["population"] >= 5e6:
            return row["days_since_100_total_cases"]
        return pd.NA

    df["days_since_100_total_cases_and_5m_pop"] = df.apply(mapper_days_since, axis=1)

    # Inject boolean when all exenplar conditions hold
    # Use int because the Grapher doesn't handle non-ints very well
    countries_with_testing_data = set(get_testing()["location"])

    def mapper_bool(row):
        if (
            pd.notnull(row["days_since_100_total_cases"])
            and pd.notnull(row["population"])
            and row["days_since_100_total_cases"] >= 21
            and row["population"] >= 5e6
            and row["location"] in countries_with_testing_data
        ):
            return 1
        return 0

    df["5m_pop_and_21_days_since_100_cases_and_testing"] = df.apply(mapper_bool, axis=1)

    return drop_population(df)


# =========================
# Doubling days calculation
# =========================

doubling_days_spec = {
    "doubling_days_total_cases_3_day_period": {
        "value_col": "total_cases",
        "periods": 3,
    },
    "doubling_days_total_cases_7_day_period": {
        "value_col": "total_cases",
        "periods": 7,
    },
    "doubling_days_total_deaths_3_day_period": {
        "value_col": "total_deaths",
        "periods": 3,
    },
    "doubling_days_total_deaths_7_day_period": {
        "value_col": "total_deaths",
        "periods": 7,
    },
}


def pct_change_to_doubling_days(pct_change, periods):
    if pd.notnull(pct_change) and pct_change != 0:
        doubling_days = periods * np.log(2) / np.log(1 + pct_change)
        return np.round(doubling_days, decimals=2)
    return pd.NA


def inject_doubling_days(df):
    for col, spec in doubling_days_spec.items():
        value_col = spec["value_col"]
        periods = spec["periods"]
        df.loc[df[value_col] == 0, value_col] = np.nan
        df[col] = (
            df.groupby("location", as_index=False)[value_col]
            .pct_change(periods=periods, fill_method=None)[value_col]
            .map(lambda pct: pct_change_to_doubling_days(pct, periods))
        )
    return df


# ====================================
# Weekly & biweekly growth calculation
# ====================================


def _inject_growth(df, prefix, periods):
    cases_colname = "%s_cases" % prefix
    deaths_colname = "%s_deaths" % prefix
    cases_growth_colname = "%s_pct_growth_cases" % prefix
    deaths_growth_colname = "%s_pct_growth_deaths" % prefix

    df[[cases_colname, deaths_colname]] = (
        df[["location", "new_cases", "new_deaths"]]
        .groupby("location")[["new_cases", "new_deaths"]]
        .rolling(window=periods, min_periods=periods - 1, center=False)
        .sum()
        .reset_index(level=0, drop=True)
    )
    df[[cases_growth_colname, deaths_growth_colname]] = (
        df[["location", cases_colname, deaths_colname]]
        .groupby("location")[[cases_colname, deaths_colname]]
        .pct_change(periods=periods, fill_method=None)
        .round(3)
        .replace([np.inf, -np.inf], pd.NA)
        * 100
    )

    df.loc[df.new_cases.isnull(), cases_colname] = np.nan
    df.loc[df.new_deaths.isnull(), deaths_colname] = np.nan

    return df


def inject_weekly_growth(df):
    return _inject_growth(df, "weekly", 7)


def inject_biweekly_growth(df):
    return _inject_growth(df, "biweekly", 14)


# ============
# Export logic
# ============

KEYS = ["date", "location"]

BASE_MEASURES = [
    "new_cases",
    "new_deaths",
    "total_cases",
    "total_deaths",
    "weekly_cases",
    "weekly_deaths",
    "biweekly_cases",
    "biweekly_deaths",
]

PER_MILLION_MEASURES = ["%s_per_million" % m for m in BASE_MEASURES]

DAYS_SINCE_MEASURES = list(days_since_spec.keys())

# Should keep these append-only in case someone external depends on the order
FULL_DATA_COLS = [*KEYS, *BASE_MEASURES]

GRAPHER_COL_NAMES = {
    "location": "Country",
    "date": "Year",
    # Absolute values
    "new_cases": "Daily new confirmed cases of COVID-19",
    "new_deaths": "Daily new confirmed deaths due to COVID-19",
    "total_cases": "Total confirmed cases of COVID-19",
    "total_deaths": "Total confirmed deaths due to COVID-19",
    # Per million
    "new_cases_per_million": "Daily new confirmed cases of COVID-19 per million people",
    "new_deaths_per_million": "Daily new confirmed deaths due to COVID-19 per million people",
    "total_cases_per_million": "Total confirmed cases of COVID-19 per million people",
    "total_deaths_per_million": "Total confirmed deaths due to COVID-19 per million people",
    # Days since
    "days_since_100_total_cases": "Days since the total confirmed cases of COVID-19 reached 100",
    "days_since_5_total_deaths": "Days since the total confirmed deaths of COVID-19 reached 5",
    "days_since_1_total_cases_per_million": (
        "Days since the total confirmed cases of COVID-19 per million people reached 1"
    ),
    "days_since_0_1_total_deaths_per_million": (
        "Days since the total confirmed deaths of COVID-19 per million people reached 0.1"
    ),
    # Rolling averages
    "new_cases_7_day_avg_right": "Daily new confirmed cases due to COVID-19 (rolling 7-day average, right-aligned)",
    "new_deaths_7_day_avg_right": "Daily new confirmed deaths due to COVID-19 (rolling 7-day average, right-aligned)",
    # Rolling averages - per million
    "new_cases_per_million_7_day_avg_right": (
        "Daily new confirmed cases of COVID-19 per million people (rolling 7-day average, right-aligned)"
    ),
    "new_deaths_per_million_7_day_avg_right": (
        "Daily new confirmed deaths due to COVID-19 per million people (rolling 7-day average, right-aligned)"
    ),
    # Case fatality ratio
    "cfr": "Case fatality rate of COVID-19 (%)",
    "cfr_100_cases": "Case fatality rate of COVID-19 (%) (Only observations with ≥100 cases)",
    # Exemplars variables
    "days_since_100_total_cases_and_5m_pop": (
        "Days since the total confirmed cases of COVID-19 reached 100 (with population ≥ 5M)"
    ),
    "5m_pop_and_21_days_since_100_cases_and_testing": (
        "Has population ≥ 5M AND had ≥100 cases ≥21 days ago AND has testing data"
    ),
    # Weekly aggregates
    "weekly_cases": "Weekly cases",
    "weekly_deaths": "Weekly deaths",
    "weekly_pct_growth_cases": "Weekly case growth (%)",
    "weekly_pct_growth_deaths": "Weekly death growth (%)",
    # Biweekly aggregates
    "biweekly_cases": "Biweekly cases",
    "biweekly_deaths": "Biweekly deaths",
    "biweekly_pct_growth_cases": "Biweekly case growth (%)",
    "biweekly_pct_growth_deaths": "Biweekly death growth (%)",
    # Weekly aggregates per capita
    "weekly_cases_per_million": "Weekly cases per million people",
    "weekly_deaths_per_million": "Weekly deaths per million people",
    # Biweekly aggregates per capita
    "biweekly_cases_per_million": "Biweekly cases per million people",
    "biweekly_deaths_per_million": "Biweekly deaths per million people",
}


def existsin(l1, l2):
    return [x for x in l1 if x in l2]


def standard_export(df, output_path, grapher_name):
    # Grapher
    df_grapher = df.copy()
    df_grapher["date"] = pd.to_datetime(df_grapher["date"]).map(lambda date: (date - zero_day).days)
    df_grapher = (
        df_grapher[GRAPHER_COL_NAMES.keys()]
        .rename(columns=GRAPHER_COL_NAMES)
        .to_csv(os.path.join(output_path, "%s.csv" % grapher_name), index=False)
    )

    # Table & public extracts for external users
    # Excludes aggregates
    excluded_aggregates = list(
        set(aggregates_spec.keys())
        - set(
            [
                "World",
                "North America",
                "South America",
                "Europe",
                "Africa",
                "Asia",
                "Oceania",
                "European Union",
                "High income",
                "Upper middle income",
                "Lower middle income",
                "Low income",
            ]
        )
    )
    df_table = df[~df["location"].isin(excluded_aggregates)]
    # full_data.csv
    full_data_cols = existsin(FULL_DATA_COLS, df_table.columns)
    df_table[full_data_cols].dropna(subset=BASE_MEASURES, how="all").to_csv(
        os.path.join(output_path, "full_data.csv"), index=False
    )
    # Pivot variables (wide format)
    for col_name in [*BASE_MEASURES, *PER_MILLION_MEASURES]:
        df_pivot = df_table.pivot(index="date", columns="location", values=col_name)
        # move World to first column
        cols = df_pivot.columns.tolist()
        cols.insert(0, cols.pop(cols.index("World")))
        df_pivot[cols].to_csv(os.path.join(output_path, "%s.csv" % col_name))
    return True


# Other
def hide_recent_zeros(df: pd.DataFrame) -> pd.DataFrame:
    last_reported_date = df.date.max()

    last_positive_cases_date = df.loc[df.new_cases > 0, "date"].max()
    if pd.isnull(last_positive_cases_date):
        return df
    if last_positive_cases_date != last_reported_date:
        last_known_cases = df.loc[df.date == last_positive_cases_date, "new_cases"].item()
        if last_known_cases >= 100 and (last_reported_date - last_positive_cases_date).days < 7:
            df.loc[df.date > last_positive_cases_date, "new_cases"] = np.nan

    last_positive_deaths_date = df.loc[df.new_deaths > 0, "date"].max()
    if pd.isnull(last_positive_deaths_date):
        return df
    if last_positive_deaths_date != last_reported_date:
        last_known_deaths = df.loc[df.date == last_positive_deaths_date, "new_deaths"].item()
        if last_known_deaths >= 10 and (last_reported_date - last_positive_deaths_date).days < 7:
            df.loc[df.date > last_positive_deaths_date, "new_deaths"] = np.nan

    return df


def discard_rows(df):
    # For all rows where new_cases or new_deaths is negative, we keep the cumulative value but set
    # the daily change to NA. This also sets the 7-day rolling average to NA for the next 7 days.
    df.loc[df.new_cases < 0, "new_cases"] = np.nan
    df.loc[df.new_deaths < 0, "new_deaths"] = np.nan

    # Custom data corrections
    for ldc in LARGE_DATA_CORRECTIONS:
        df.loc[(df.location == ldc[0]) & (df.date.astype(str) == ldc[1]), f"new_{ldc[2]}"] = np.nan

    # If the last known value is above 1000 cases or 100 deaths but the latest reported value is 0
    # then set that value to NA in case it's a temporary reporting error. (Up to 7 days in the past)
    df = df.sort_values(["location", "date"]).groupby("location").apply(hide_recent_zeros)

    return df
