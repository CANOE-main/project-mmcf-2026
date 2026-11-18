import pandas as pd

# Population is in the same file but is national only; CANOE's default StatCan
# population is provincial, so it is kept instead.


def process_cef_gdp_csv():
    cef_series = pd.read_csv("data/cef2026series_oct21_mmcf_r3.csv")
    cef_series.columns = ["parameter", "region", "year", "value", "unit", "notes"]
    gdp = cef_series[cef_series["parameter"] == "Real Gross Domestic Product"].copy()
    gdp["source"] = "CER Canada's Energy Future 2026, real GDP of Canada (Canada Net-zero)"

    gdp[["region", "year", "value", "source"]].to_csv("data/cef2026_gdp.csv", index=False)


if __name__ == "__main__":
    process_cef_gdp_csv()
