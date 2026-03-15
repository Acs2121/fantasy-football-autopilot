DRAFT_TYPES = ["snake", "best_ball", "dynasty", "auction"]

DEFAULT_CONFIG = {
    "draft_type": "snake",
    "num_teams": 12,
    "num_rounds": 15,
    "snake": True,
    "user_team_index": 0,  # 0-based, pick 1 = index 0
    "roster_slots": {
        "QB": 1,
        "RB": 2,
        "WR": 2,
        "TE": 1,
        "FLEX": 1,
        "K": 1,
        "DST": 1,
        "BN": 6,
    },

    # Auction-specific settings (used when draft_type == "auction")
    "auction": {
        "budget_per_team": 200,
        "min_bid": 1,
        "nomination_order": "snake",  # "snake" or "linear"
    },

    # Dynasty/keeper-specific settings (used when draft_type == "dynasty")
    "dynasty": {
        "keeper_slots": 0,
        "rookie_only_rounds": 0,  # 0 = full open draft; N = only rookies after round N
        "keepers": {},            # {team_index: [player_id, ...]}
    },
}
