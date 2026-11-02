import pandas as pd

def process_emh_csv():
    cef_series = pd.read_csv("data/cef2026series_oct21_mmcf_r3.csv")
    cef_series.columns = ["parameter", "region", "year", "price", "unit", "notes"]
    energy_prices = cef_series[cef_series["parameter"].str.startswith("End-use price - ")]
    energy_prices["fuel"] = energy_prices.parameter.apply(lambda s: s.split(" - ")[1].strip(" "))
    energy_prices["sector"] = energy_prices.parameter.apply(lambda s: s.split(" - ")[2].strip(" "))
    energy_prices["currency"] = "CAD"
    energy_prices["currency_year"] = energy_prices.unit.apply(lambda s: int(s.split(" ")[0]))
    energy_prices["units"] = "$/GJ"
    energy_prices = energy_prices[energy_prices.fuel != "Electricity"]
    energy_prices = energy_prices[~energy_prices.region.isin(["Yukon", "Nunavut", "Northwest Territories"])]

    required_cols = [
        "region",
        "sector",
        "fuel",
        "year",
        "price",
        "currency",
        "currency_year",
        "units",
    ]
    energy_prices[required_cols].to_csv("data/cef2026_fuel_prices.csv", index=False)


if __name__ == "__main__":
    process_emh_csv()
