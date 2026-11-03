"""
Add the CER
"""

import pandas as pd
from canoe.common import atomic_transaction

DEFAULT_CARBON_PRICE = {
    2020: 115,
    2030: 130,
    2035: 140,
    2040: 140,
    2045: 140
}


def add_cer(
    db: str,
    cer_comm: str = 'co2_cer',
    source_emis_comm: str = 'co2',
    commodity_data_id: str = 'ELCHR003',
    fuel_vintage: int = 2025,
    exclude_tech_pattern: str = '%RFIT%',
    exempt_tech_pattern: str = r'%\_CG-%',
    periods: range = range(2035, 2046, 5),
    final_period: int = 2045,
    grandfather_years: int = 25,
    new_vintage_from: int = 2030,
    cap: float = 65,
    final_cap: float = 0,
    credit_limit: float = 35,
    final_credit_limit: float = 42,
    carbon_price: dict[int, float] | None = None,
    price_dollar_year: int = 2026,
    deflator_to_2020: float = 1.2418,
    inflation_rate: float = 0.02,
) -> None:
    """
    Add the Clean Electricity Regulation (CER).

    :param db: Path to the SQLite database.
    :param cer_comm: Dummy emission commodity carrying net CER emissions.
    :param source_emis_comm: Emission commodity the CER emissions are derived from.
    :param commodity_data_id: data_id for the new commodity row.
    :param fuel_vintage: Vintage of the F_E_* fuel techs used for fuel emission factors.
    :param exclude_tech_pattern: LIKE pattern of E_* techs whose own emissions are ignored.
    :param exempt_tech_pattern: LIKE pattern (escape char '\\') of E_* techs fully exempt
        from the CER (no CER emissions or caps), e.g. cogeneration.
    :param periods: Periods the CER applies to.
    :param final_period: Period where the final cap and credit limit apply.
    :param grandfather_years: Units are covered once period >= vintage + grandfather_years.
    :param new_vintage_from: Vintages from this year onward are always covered.
    :param cap: Emission cap (t/GWh) before final_period.
    :param final_cap: Emission cap (t/GWh) in final_period.
    :param credit_limit: Credit limit (t/GWh) before final_period.
    :param final_credit_limit: Credit limit (t/GWh) in final_period.
    :param carbon_price: Credit price ($/t, nominal) by period.
    :param price_dollar_year: Dollar year of carbon_price; prices after this are deflated.
    :param deflator_to_2020: Price index ratio price_dollar_year / 2020.
    :param inflation_rate: Assumed annual inflation after price_dollar_year.
    """
    if carbon_price is None:
        carbon_price = DEFAULT_CARBON_PRICE

    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clean_electricity_regulation
            (
                region        TEXT,
                period        INTEGER,
                tech          TEXT,
                vintage       INTEGER,
                emis_comm     TEXT,
                cap           REAL NOT NULL,
                credit_limit  REAL NOT NULL DEFAULT 0,
                credit_cost   REAL NOT NULL DEFAULT 0,
                units         TEXT,
                notes         TEXT,
                PRIMARY KEY (region, period, tech, vintage, emis_comm),
                FOREIGN KEY (period) REFERENCES time_period (period),
                FOREIGN KEY (vintage) REFERENCES time_period (period),
                FOREIGN KEY (emis_comm) REFERENCES commodity_label (commodity)
            );
            """
        )
        curs.execute(
            """
            REPLACE INTO commodity_label(commodity) VALUES(?)
            """,
            (cer_comm,)
        )
        curs.execute(
            """
            REPLACE INTO commodity(name, flag, description, data_id)
            VALUES(?, 'e', 'CER dummy co2 duplicate for policy modelling', ?)
            """,
            (cer_comm, commodity_data_id)
        )
        df_fuel_emissions = pd.read_sql_query(
            """
            SELECT DISTINCT output_comm, activity
            FROM emission_activity
            WHERE tech LIKE 'F_E_%'
                AND emis_comm == ?
                AND vintage == ?;
            """,
            conn,
            params=(source_emis_comm, fuel_vintage)
        )
        df_efficiency = pd.read_sql_query(
            """
            SELECT DISTINCT region, input_comm, tech, vintage, output_comm, efficiency
            FROM efficiency
            WHERE tech LIKE 'E_%'
                AND tech NOT LIKE ? ESCAPE '\\'
                AND input_comm IN (
                    SELECT DISTINCT output_comm
                    FROM emission_activity
                    WHERE tech LIKE 'F_E_%'
                    AND emis_comm == ?
                    AND vintage == ?
                )
            """,
            conn,
            params=(exempt_tech_pattern, source_emis_comm, fuel_vintage)
        )
        df_emission_activity = pd.read_sql_query(
            """
            SELECT DISTINCT tech, vintage, activity
            FROM emission_activity
            WHERE tech LIKE 'E_%'
                AND tech NOT LIKE ?
                AND emis_comm == ?;
            """,
            conn,
            params=(exclude_tech_pattern, source_emis_comm)
        )

        # Calculate net co2 emissions
        df_efficiency['activity'] = df_efficiency.apply(
            lambda row: df_fuel_emissions.loc[
                df_fuel_emissions['output_comm'] == row['input_comm'], 'activity'
            ].values[0] / row['efficiency'] + df_emission_activity.loc[
                (df_emission_activity['tech'] == row['tech'])
                & (df_emission_activity['vintage'] == row['vintage']),
                'activity'
            ].sum(),
            axis=1
        )
        df_efficiency['emis_comm'] = cer_comm
        df_efficiency['units'] = 'kt/PJ'
        df_efficiency['notes'] = 'CER net co2 dummy emitter'
        df_ea_cer = df_efficiency[[
            'region',
            'emis_comm',
            'input_comm',
            'tech',
            'vintage',
            'output_comm',
            'activity',
            'units',
            'notes'
        ]]
        df_ea_cer.to_sql('emission_activity', conn, if_exists='append', index=False)
        # duplicate all elements of this table for each CER period
        for year in periods:
            df = df_ea_cer.copy()
            df['period'] = year
            df = df.loc[
                (df['period'] >= df['vintage'] + grandfather_years)
                | (df['vintage'] >= new_vintage_from)
                | (df['tech'].str.contains('COAL'))
                | (df['period'] == final_period)
            ]
            df['tech_or_group'] = df['tech']
            factor = 8760/1000 # t/8760GW to kt/GW
            df.loc[df['period'] < final_period, 'cap'] = cap * factor
            df.loc[df['period'] == final_period, 'cap'] = final_cap * factor
            df.loc[df['period'] < final_period, 'credit_limit'] = credit_limit * factor
            df.loc[df['period'] == final_period, 'credit_limit'] = final_credit_limit * factor
            credit_price = carbon_price[year] / deflator_to_2020 # price_dollar_year -> 2020 dollars
            if year > price_dollar_year:
                credit_price /= (1 + inflation_rate)**(year-price_dollar_year) # assumed inflation thereafter
            credit_price /= 1000 # $/t to M$/kt
            df['credit_cost'] = credit_price
            df['notes'] = 'CER regulations by vintage and period'
            df = df[[
                'region',
                'period',
                'tech',
                'vintage',
                'emis_comm',
                'cap',
                'credit_limit',
                'credit_cost',
                'notes'
            ]]
            df.to_sql('clean_electricity_regulation', conn, if_exists='append', index=False)
        conn.commit()


if __name__ == "__main__":
    add_cer("canoe-pwd/canoe.sqlite")
