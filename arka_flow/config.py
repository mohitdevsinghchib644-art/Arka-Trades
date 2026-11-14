"""Constants. Anything marked PROVISIONAL is a judgement call that Phase 6 must test before it is trusted."""

MODEL_VERSION = "0.2.0"
NA = "NOT AVAILABLE"

# Fixed by the project brief (not tunable)
HORIZONS = (5, 10, 20, 40, 60, 120, 252)      # sessions
EMA_SPANS = (10, 20, 50, 100, 200)
SMA_SPANS = (20, 50, 200)
BREADTH_DELTAS = (1, 5, 10, 20)               # sessions

# PROVISIONAL - not validated
MIN_SECTOR_COVERAGE = 0.80        # below this share of members with prices a sector is flagged INCOMPLETE
MIN_BREADTH_MOVE_STOCKS = 2       # a breadth change smaller than this many stocks is called "steady"
BREADTH_BROAD, BREADTH_NARROW = 60.0, 40.0   # % above 50-EMA called broad / narrow in descriptions
EXTENDED_PCTILE = 90.0            # distance from 50-EMA in the top 10% of the sector's own last 252 sessions
LEADER_PCTILE = 100 * 2 / 3       # top third of the universe by relative return
LEADER_CONSISTENCY = 0.60         # share of the last 60 sessions spent in the top third = "persistent"
RANK_MOVE = 15.0                  # percentile points: "improving" / "weakening" rank change
JUMP_PCT = 25.0                   # one-session move flagged for corporate-action review (reported, never auto-fixed)

# NSE cash market hours, IST
SESSION_OPEN = (9, 15)
SESSION_CLOSE = (15, 30)
SESSION_SETTLE_MIN = 15           # minutes after the close before a daily bar is treated as final
