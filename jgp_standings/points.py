import polars as pl


def assign_standings_points(df, place, points, secPoints, standings_points, tie_break):
    """Allocate points in descending match-point, quality-point and tie-break order.

    Tied players take successive list entries. Ranked players with negative
    quality points score zero and consume no slot. The descending unique
    `tie_break` matches the final key in `generation_core.rank_band`.
    """
    standings_points_list = [25, 22] + list(range(20, 11, -2)) + list(range(11, 0, -1))

    df_sorted = df.sort([points, secPoints, tie_break], descending=[True, True, True])

    rows_data = df_sorted.to_dicts()
    standings_points_assigned = [None] * len(rows_data)
    for i in range(len(rows_data)):
        if rows_data[i][place] is not None and rows_data[i][secPoints] is not None and rows_data[i][secPoints] < 0:
            standings_points_assigned[i] = 0
    index = 0  # index into standings_points_list
    for i in range(len(rows_data)):
        # Skip players with null place (did not participate in this tournament)
        if rows_data[i][place] is None:
            continue

        # Skip penalized players (already assigned 0, do not consume a slot)
        if standings_points_assigned[i] == 0:
            continue

        if index < len(standings_points_list):
            standings_points_assigned[i] = standings_points_list[index]
        else:
            standings_points_assigned[i] = 1

        index += 1

    # Assign 1 point to any remaining players with non-null secPoints not yet assigned
    for k in range(len(rows_data)):
        if standings_points_assigned[k] is None and rows_data[k][secPoints] is not None:
            standings_points_assigned[k] = 1

    df_sorted = df_sorted.with_columns([
        pl.Series(name=standings_points, values=standings_points_assigned, dtype=pl.Float64)
    ])

    return df_sorted