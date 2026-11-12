"""
Make existing cogeneration plants flexible
"""

from canoe.common import atomic_transaction


def add_cogen_flex(
    db: str,
    tech: str = 'E_NG_CG-EXS',
) -> None:
    """
    Flag existing cogeneration technology as flexible.

    :param db: Path to the SQLite database.
    :param tech: Technology to flag as flexible.
    """
    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        curs.execute(
            """
            UPDATE technology SET flex = 1 WHERE tech == ?
            """,
            (tech,)
        )
        conn.commit()


if __name__ == "__main__":
    add_cogen_flex("canoe-pwd/canoe.sqlite")
