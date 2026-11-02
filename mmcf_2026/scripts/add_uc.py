"""
Add CODERs unit commitment data
"""

import pandas as pd
from canoe.common import atomic_transaction


def add_uc(
    db: str,
    df_uc: pd.DataFrame,
    capacity_units: str = 'GW',
    startup_cost_units: str = 'M$/GW',
) -> None:
    """
    Add unit commitment data, applied to every region where the tech exists.

    :param db: Path to the SQLite database.
    :param df_uc: Unit commitment data, one row per tech, with columns tech, unit_capacity,
        min_output_fraction, max_output_fraction, min_up_time_hours, min_down_time_hours,
        linearized, cost_per_cap, notes.
    :param capacity_units: Units label for the unit_commitment rows.
    :param startup_cost_units: Units label for the unit_commitment_startup_cost rows.
    """
    df_uc = df_uc.copy()

    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS unit_commitment
            (
                region               TEXT    NOT NULL,
                tech                 TEXT    NOT NULL,
                unit_capacity        REAL    NOT NULL,
                min_output_fraction  REAL    NOT NULL DEFAULT 0.0,
                max_output_fraction  REAL    NOT NULL DEFAULT 1.0,
                min_up_time_hours    INTEGER NOT NULL DEFAULT 0,
                min_down_time_hours  INTEGER NOT NULL DEFAULT 0,
                linearized           INTEGER NOT NULL DEFAULT 0,
                units                TEXT,
                notes                TEXT,
                CHECK (unit_capacity > 0),
                CHECK (0 <= min_output_fraction AND min_output_fraction <= 1),
                CHECK (0 < max_output_fraction AND max_output_fraction <= 1),
                CHECK (min_output_fraction <= max_output_fraction),
                CHECK (min_up_time_hours >= 0),
                CHECK (min_down_time_hours >= 0),
                CHECK (linearized IN (0, 1)),
                PRIMARY KEY (region, tech)
            );
            CREATE TABLE IF NOT EXISTS unit_commitment_startup_cost
            (
                region                          TEXT NOT NULL,
                tech                            TEXT NOT NULL,
                cost_per_cap                    REAL NOT NULL,
                units                           TEXT,
                notes                           TEXT,
                CHECK (cost_per_cap > 0),
                PRIMARY KEY (region, tech)
            );
            CREATE TABLE IF NOT EXISTS unit_commitment_startup_emissions
            (
                region                          TEXT NOT NULL,
                emis_comm                       TEXT NOT NULL,
                tech                            TEXT NOT NULL,
                emis_per_cap                    REAL NOT NULL,
                units                           TEXT,
                notes                           TEXT,
                CHECK (emis_per_cap > 0),
                PRIMARY KEY (region, emis_comm, tech)
            );
            CREATE TABLE IF NOT EXISTS unit_commitment_startup_input
            (
                region                          TEXT NOT NULL,
                input_comm                      TEXT NOT NULL,
                tech                            TEXT NOT NULL,
                input_per_cap                   REAL NOT NULL,
                units                           TEXT,
                notes                           TEXT,
                CHECK (input_per_cap > 0),
                PRIMARY KEY (region, input_comm, tech)
            );
            CREATE TABLE IF NOT EXISTS output_unit_commitment
            (
                scenario        TEXT,
                region          TEXT,
                sector          TEXT
                    REFERENCES sector_label (sector),
                period          INTEGER
                    REFERENCES time_period (period),
                season          TEXT
                    REFERENCES time_season (season),
                tod             TEXT
                    REFERENCES time_of_day (tod),
                tech            TEXT
                    REFERENCES technology_label (tech),
                vintage         INTEGER
                    REFERENCES time_period (period),
                online_cap      REAL,
                start_cap       REAL,
                stop_cap        REAL,
                units           TEXT,
                PRIMARY KEY (scenario, region, sector, period, season, tod, tech, vintage)
            );
            """
        )

        regions = [r[0] for r in curs.execute("SELECT region FROM region").fetchall()]
        for region in regions:
            df_uc['region'] = region
            df_uc['units'] = capacity_units
            df_uc[[
                'region',
                'tech',
                'unit_capacity',
                'min_output_fraction',
                'max_output_fraction',
                'min_up_time_hours',
                'min_down_time_hours',
                'linearized',
                'units',
                'notes'
            ]].to_sql('unit_commitment',conn,if_exists='append',index=False)
            df_uc['units'] = startup_cost_units
            df_uc[[
                'region',
                'tech',
                'cost_per_cap',
                'units',
                'notes'
            ]].to_sql('unit_commitment_startup_cost',conn,if_exists='append',index=False)
        # cleanup bad rows
        curs.execute('DELETE FROM unit_commitment WHERE (region, tech) not in (select region, tech from efficiency)')
        curs.execute('DELETE FROM unit_commitment_startup_cost WHERE (region, tech) not in (select region, tech from efficiency)')
        conn.commit()


if __name__ == "__main__":
    add_uc("canoe-pwd/canoe.sqlite", pd.read_csv("data/unit_commitment_data.csv"))
