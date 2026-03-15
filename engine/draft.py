"""
Core draft state management.

Supports snake, best-ball, dynasty/keeper, and auction draft types.
"""

from config import DEFAULT_CONFIG
from engine.rankings import load_players, compute_vbd


class DraftState:
    def __init__(self, config=None):
        self.config = config or dict(DEFAULT_CONFIG)
        self.draft_type = self.config.get("draft_type", "snake")
        self.num_teams = self.config["num_teams"]
        self.num_rounds = self.config["num_rounds"]
        self.snake = self.config.get("snake", True)
        self.user_team_index = self.config["user_team_index"]
        self.roster_slots = dict(self.config["roster_slots"])

        # Load and rank players
        self.all_players = load_players()
        compute_vbd(self.all_players, self.roster_slots, self.num_teams)
        self.player_map = {p["id"]: p for p in self.all_players}

        # Draft state
        self.picks = []  # list of {round, pick, team_index, player_id}
        self.available_ids = {p["id"] for p in self.all_players}
        self.team_rosters = {i: [] for i in range(self.num_teams)}

        # Dynasty/keeper setup
        self.keeper_player_ids = set()
        dynasty_cfg = self.config.get("dynasty", {})
        self._keeper_slots = dynasty_cfg.get("keeper_slots", 0) if self.draft_type == "dynasty" else 0
        self._rookie_only_rounds = dynasty_cfg.get("rookie_only_rounds", 0) if self.draft_type == "dynasty" else 0
        if self.draft_type == "dynasty" and dynasty_cfg.get("keepers"):
            self.load_keepers(dynasty_cfg["keepers"])

        # Auction state
        auction_cfg = self.config.get("auction", {})
        is_auction = self.draft_type == "auction"
        self._budget_per_team = auction_cfg.get("budget_per_team", 200) if is_auction else 0
        self._min_bid = auction_cfg.get("min_bid", 1)
        self._nomination_order = auction_cfg.get("nomination_order", "snake") if is_auction else "snake"
        self.team_budgets = {i: self._budget_per_team for i in range(self.num_teams)} if is_auction else {}
        self.current_nomination_team = 0
        self._nomination_idx = 0
        self.active_auction = None
        self.auction_results = []
        self._all_teams_set = frozenset(range(self.num_teams))

    # -- Core properties ------------------------------------------------------

    @property
    def current_pick_number(self):
        return len(self.picks) + 1

    @property
    def current_round(self):
        return len(self.picks) // self.num_teams + 1

    @property
    def draft_complete(self):
        if self.draft_type == "auction":
            if not self.available_ids:
                return True
            return len(self.auction_results) >= self.num_teams * self.num_rounds
        return len(self.picks) >= self.num_teams * self.num_rounds

    def team_for_pick(self, pick_index):
        """Return team index for a 0-based pick index, handling snake order."""
        round_num = pick_index // self.num_teams
        pos_in_round = pick_index % self.num_teams
        if self.snake and round_num % 2 == 1:
            return self.num_teams - 1 - pos_in_round
        return pos_in_round

    @property
    def current_team_index(self):
        return self.team_for_pick(len(self.picks))

    @property
    def is_user_pick(self):
        if self.draft_type == "auction":
            if self.active_auction is not None:
                auc = self.active_auction
                return (self.user_team_index not in auc["passed"]
                        and auc["current_bidder"] != self.user_team_index)
            return self.current_nomination_team == self.user_team_index
        return self.current_team_index == self.user_team_index

    def picks_until_user_turn(self):
        """How many picks until the user picks next. O(1) via modular arithmetic."""
        if self.draft_complete:
            return -1
        idx = len(self.picks)
        current_round = idx // self.num_teams
        pos_in_round = idx % self.num_teams

        def user_pos_in_round(round_num):
            if self.snake and round_num % 2 == 1:
                return self.num_teams - 1 - self.user_team_index
            return self.user_team_index

        user_pos = user_pos_in_round(current_round)
        if pos_in_round <= user_pos:
            return user_pos - pos_in_round
        return (self.num_teams - pos_in_round) + user_pos_in_round(current_round + 1)

    # -- Pick management ------------------------------------------------------

    def make_pick(self, player_id, team_index=None):
        if self.draft_complete:
            return {"error": "Draft is complete"}
        if player_id not in self.available_ids:
            return {"error": "Player not available"}

        if team_index is None:
            team_index = self.current_team_index

        pick_record = {
            "round": self.current_round,
            "pick": self.current_pick_number,
            "team_index": team_index,
            "player_id": player_id,
        }
        self.picks.append(pick_record)
        self.available_ids.remove(player_id)
        self.team_rosters[team_index].append(player_id)
        return pick_record

    def undo_last_pick(self):
        if not self.picks:
            return {"error": "No picks to undo"}
        pick = self.picks.pop()
        self.available_ids.add(pick["player_id"])
        self.team_rosters[pick["team_index"]].remove(pick["player_id"])
        return pick

    # -- Dynasty/keeper -------------------------------------------------------

    def load_keepers(self, keepers):
        """Pre-fill team rosters with keeper players before the draft."""
        self.keeper_player_ids = set()
        for team_str, pids in keepers.items():
            team_idx = int(team_str)
            if team_idx not in self.team_rosters:
                continue
            for pid in pids:
                pid = int(pid)
                if pid not in self.player_map or pid in self.keeper_player_ids:
                    continue
                self.team_rosters[team_idx].append(pid)
                self.available_ids.discard(pid)
                self.keeper_player_ids.add(pid)
                self.player_map[pid]["is_keeper"] = True

    @property
    def is_rookie_only_round(self):
        if self._rookie_only_rounds <= 0:
            return False
        return self.current_round > self._rookie_only_rounds

    # -- Player access --------------------------------------------------------

    def get_available_players(self, position=None):
        """Return available players sorted by VBD, optionally filtered."""
        players = [self.player_map[pid] for pid in self.available_ids]
        if self.is_rookie_only_round:
            players = [p for p in players if p.get("is_rookie", False)]
        if position:
            players = [p for p in players if p["position"] == position]
        players.sort(key=lambda x: x.get("vbd_score", 0), reverse=True)
        return players

    def get_team_roster(self, team_index):
        return [self.player_map[pid] for pid in self.team_rosters[team_index]]

    def get_positional_counts(self, team_index):
        """Count how many of each position a team has drafted.

        Uses player_map directly instead of building full player objects.
        """
        counts = {}
        for pid in self.team_rosters[team_index]:
            pos = self.player_map[pid]["position"]
            counts[pos] = counts.get(pos, 0) + 1
        return counts

    # -- Auction methods ------------------------------------------------------

    def can_afford(self, team_index, amount):
        budget = self.team_budgets.get(team_index, 0)
        if amount > budget:
            return False
        roster_size = len(self.team_rosters.get(team_index, []))
        remaining_slots = self.num_rounds - roster_size - 1
        return budget - amount >= remaining_slots * self._min_bid

    def start_nomination(self, player_id, opening_bid, team_index):
        if self.draft_type != "auction":
            return {"error": "Not in auction mode"}
        if self.active_auction is not None:
            return {"error": "An auction is already in progress"}
        if self.draft_complete:
            return {"error": "Draft is complete"}
        if player_id not in self.available_ids:
            return {"error": "Player not available"}
        if opening_bid < self._min_bid:
            return {"error": f"Minimum bid is ${self._min_bid}"}
        if not self.can_afford(team_index, opening_bid):
            return {"error": "Cannot afford this bid"}

        self.active_auction = {
            "player_id":      player_id,
            "nominator":      team_index,
            "current_bid":    opening_bid,
            "current_bidder": team_index,
            "passed":         set(),
        }
        return {"ok": True, "auction": self._auction_dict()}

    def place_bid(self, team_index, amount):
        if self.active_auction is None:
            return {"error": "No active auction"}
        if team_index == self.active_auction["current_bidder"]:
            return {"error": "You are already the high bidder"}
        if amount <= self.active_auction["current_bid"]:
            return {"error": f"Bid must exceed ${self.active_auction['current_bid']}"}
        if not self.can_afford(team_index, amount):
            return {"error": "Cannot afford this bid"}

        self.active_auction["current_bid"]    = amount
        self.active_auction["current_bidder"] = team_index
        self.active_auction["passed"].discard(team_index)
        return {"ok": True, "auction": self._auction_dict()}

    def pass_bid(self, team_index):
        if self.active_auction is None:
            return {"error": "No active auction"}
        self.active_auction["passed"].add(team_index)
        remaining = self._all_teams_set - self.active_auction["passed"] - {self.active_auction["current_bidder"]}
        if not remaining:
            return self.close_auction()
        return {"ok": True, "auction": self._auction_dict(), "waiting": True}

    def close_auction(self):
        if self.active_auction is None:
            return {"error": "No active auction"}
        auc    = self.active_auction
        pid    = auc["player_id"]
        winner = auc["current_bidder"]
        price  = auc["current_bid"]

        self.available_ids.discard(pid)
        self.team_rosters[winner].append(pid)
        self.team_budgets[winner] -= price

        result = {
            "player_id":  pid,
            "team_index": winner,
            "price":      price,
            "player":     self.player_map[pid],
        }
        self.auction_results.append(result)
        self.active_auction = None
        self._next_nomination_team()
        return {"ok": True, "result": result}

    def _next_nomination_team(self):
        self._nomination_idx += 1
        n = self.num_teams
        if self._nomination_order == "snake":
            cycle = self._nomination_idx // n
            pos   = self._nomination_idx % n
            self.current_nomination_team = (n - 1 - pos) if cycle % 2 == 1 else pos
        else:
            self.current_nomination_team = self._nomination_idx % n

    def _auction_dict(self):
        """Serialize active_auction for the API."""
        if self.active_auction is None:
            return None
        a = dict(self.active_auction)
        a["passed"] = list(a["passed"])
        a["player"] = self.player_map.get(a["player_id"])
        return a

    @property
    def is_user_nomination_turn(self):
        return (self.draft_type == "auction"
                and not self.draft_complete
                and self.active_auction is None
                and self.current_nomination_team == self.user_team_index)

    # -- State serialization --------------------------------------------------

    def get_state_dict(self):
        """Return full state as a dict for the API."""
        pm = self.player_map
        d = {
            "draft_type": self.draft_type,
            "num_teams": self.num_teams,
            "num_rounds": self.num_rounds,
            "snake": self.snake,
            "user_team_index": self.user_team_index,
            "roster_slots": self.roster_slots,
            "current_pick": self.current_pick_number,
            "current_round": self.current_round,
            "current_team_index": self.current_team_index,
            "is_user_pick": self.is_user_pick,
            "picks_until_user": self.picks_until_user_turn(),
            "draft_complete": self.draft_complete,
            "picks": [
                {**pick, "player": pm[pick["player_id"]]}
                for pick in self.picks
            ],
            "user_roster": [pm[pid] for pid in self.team_rosters[self.user_team_index]],
            "team_rosters": {
                str(i): [pm[pid] for pid in pids]
                for i, pids in self.team_rosters.items()
            },
        }

        if self.draft_type == "dynasty":
            d["keeper_player_ids"]    = list(self.keeper_player_ids)
            d["is_rookie_only_round"] = self.is_rookie_only_round
            d["keeper_slots"]         = self._keeper_slots

        if self.draft_type == "auction":
            d.update({
                "team_budgets":            self.team_budgets,
                "current_nomination_team":  self.current_nomination_team,
                "is_user_nomination_turn":  self.is_user_nomination_turn,
                "active_auction":           self._auction_dict(),
                "auction_results":          self.auction_results,
                "budget_per_team":          self._budget_per_team,
                "min_bid":                  self._min_bid,
                "picks": [
                    {"team_index": r["team_index"], "player_id": r["player_id"],
                     "price": r["price"], "player": r["player"]}
                    for r in self.auction_results
                ],
            })

        return d
