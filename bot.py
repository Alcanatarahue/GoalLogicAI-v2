import os
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ============================================================
# GOALLOGIC AI v3.3
# ============================================================

VERSION = "3.3"

API_BASE = "https://openfootapi.com/v1"
SEASON = "2026/27"
SAMPLE_SIZE = 5

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY")

PORT = int(os.getenv("PORT", "10000"))


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(
            f"GoalLogic AI v{VERSION} is running.".encode()
        )

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


threading.Thread(
    target=start_health_server,
    daemon=True
).start()


# ============================================================
# API
# ============================================================

def api_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        return None, "OPENFOOT_API_KEY is missing."

    url = f"{API_BASE}{endpoint}"

    headers = {
        "Authorization": f"Bearer {OPENFOOT_API_KEY}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params or {},
            timeout=20,
        )

        if response.status_code != 200:
            return None, f"API HTTP {response.status_code}: {response.text[:300]}"

        data = response.json()
        return data, None

    except Exception as e:
        return None, f"API request error: {e}"


# ============================================================
# TEAM SEARCH
# ============================================================

def search_team(query):
    data, error = api_get(
        "/search",
        {"q": query}
    )

    if error:
        return [], error

    teams = data.get("data", [])

    if not isinstance(teams, list):
        return [], "Unexpected team search response."

    return teams, None


def team_name(team):
    if not isinstance(team, dict):
        return str(team)

    return (
        team.get("name")
        or team.get("shortName")
        or team.get("displayName")
        or team.get("title")
        or "Unknown"
    )


def team_id(team):
    if not isinstance(team, dict):
        return None

    return (
        team.get("id")
        or team.get("teamId")
        or team.get("team_id")
    )


def find_team(query):
    teams, error = search_team(query)

    if error:
        return None, error

    if not teams:
        return None, f"No team found for '{query}'."

    query_lower = query.lower().strip()

    # Exact name first
    for team in teams:
        if team_name(team).lower().strip() == query_lower:
            return team, None

    # Then partial match
    for team in teams:
        if query_lower in team_name(team).lower():
            return team, None

    return teams[0], None


# ============================================================
# SCORE EXTRACTION
# ============================================================

def number_value(value):
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        value = value.strip()

        try:
            return int(float(value))
        except Exception:
            return None

    return None


def extract_score_side(obj, keys):
    if not isinstance(obj, dict):
        return None

    for key in keys:
        if key in obj:
            value = number_value(obj.get(key))
            if value is not None:
                return value

    return None


def extract_scores(match):
    if not isinstance(match, dict):
        return None, None

    home_score_keys = [
        "homeScore",
        "home_score",
        "homeGoals",
        "home_goals",
    ]

    away_score_keys = [
        "awayScore",
        "away_score",
        "awayGoals",
        "away_goals",
    ]

    home = extract_score_side(match, home_score_keys)
    away = extract_score_side(match, away_score_keys)

    if home is not None and away is not None:
        return home, away

    score = match.get("score")

    if isinstance(score, dict):
        home = extract_score_side(
            score,
            [
                "home",
                "homeScore",
                "home_score",
                "homeGoals",
            ],
        )

        away = extract_score_side(
            score,
            [
                "away",
                "awayScore",
                "away_score",
                "awayGoals",
            ],
        )

        if home is not None and away is not None:
            return home, away

        home_obj = score.get("home")
        away_obj = score.get("away")

        if isinstance(home_obj, dict):
            home = extract_score_side(
                home_obj,
                ["score", "goals", "value", "current"]
            )

        if isinstance(away_obj, dict):
            away = extract_score_side(
                away_obj,
                ["score", "goals", "value", "current"]
            )

        if home is not None and away is not None:
            return home, away

    scores = match.get("scores")

    if isinstance(scores, dict):
        home = extract_score_side(
            scores,
            ["home", "homeScore", "home_score"]
        )

        away = extract_score_side(
            scores,
            ["away", "awayScore", "away_score"]
        )

        if home is not None and away is not None:
            return home, away

    return None, None


# ============================================================
# MATCH HELPERS
# ============================================================

def match_kickoff(match):
    return (
        match.get("kickoffAt")
        or match.get("kickoff")
        or match.get("date")
        or ""
    )


def match_status(match):
    status = match.get("status")

    if isinstance(status, dict):
        return (
            status.get("type")
            or status.get("name")
            or status.get("short")
            or ""
        ).lower()

    return str(status or "").lower()


def is_completed(match):
    home, away = extract_scores(match)

    if home is None or away is None:
        return False

    status = match_status(match)

    unfinished_words = [
        "scheduled",
        "upcoming",
        "pending",
        "postponed",
        "cancelled",
        "canceled",
        "live",
        "inplay",
        "not_started",
    ]

    if any(word in status for word in unfinished_words):
        return False

    return True


def get_match_home_team(match):
    home = match.get("homeTeam")

    if isinstance(home, dict):
        return team_name(home)

    return str(home or "Unknown")


def get_match_away_team(match):
    away = match.get("awayTeam")

    if isinstance(away, dict):
        return team_name(away)

    return str(away or "Unknown")


def get_team_matches(team_id_value):
    data, error = api_get(
        "/matches",
        {
            "team": team_id_value,
            "season": SEASON,
        },
    )

    if error:
        return [], error

    matches = data.get("data", [])

    if not isinstance(matches, list):
        return [], "Unexpected matches response."

    completed = [
        match
        for match in matches
        if is_completed(match)
    ]

    completed.sort(
        key=match_kickoff,
        reverse=True
    )

    return completed, None


# ============================================================
# TEAM-ORIENTED MATCH DATA
# ============================================================

def same_team(match_team, target_team):
    if not match_team or not target_team:
        return False

    a = str(match_team).lower().strip()
    b = str(target_team).lower().strip()

    return a == b


def team_oriented_matches(matches, target_team_id, target_team_name):
    result = []

    for match in matches:
        home_obj = match.get("homeTeam")
        away_obj = match.get("awayTeam")

        home_id = None
        away_id = None

        if isinstance(home_obj, dict):
            home_id = team_id(home_obj)

        if isinstance(away_obj, dict):
            away_id = team_id(away_obj)

        home_name = get_match_home_team(match)
        away_name = get_match_away_team(match)

        if (
            home_id == target_team_id
            or away_id == target_team_id
            or same_team(home_name, target_team_name)
            or same_team(away_name, target_team_name)
        ):
            home_score, away_score = extract_scores(match)

            if home_score is None or away_score is None:
                continue

            if home_id == target_team_id or same_team(
                home_name,
                target_team_name
            ):
                is_home = True
            else:
                is_home = False

            if is_home:
                gf = home_score
                ga = away_score
            else:
                gf = away_score
                ga = home_score

            result.append(
                {
                    "match": match,
                    "is_home": is_home,
                    "gf": gf,
                    "ga": ga,
                    "total": gf + ga,
                    "date": match_kickoff(match),
                    "opponent": (
                        away_name if is_home else home_name
                    ),
                }
            )

    result.sort(
        key=lambda x: x["date"],
        reverse=True
    )

    return result


# ============================================================
# WEIGHTING
# ============================================================

def recency_weights(count):
    """
    More recent matches receive greater weight.

    For 5 matches:
    newest = 1.00
    oldest = 0.60
    """

    if count <= 0:
        return []

    if count == 1:
        return [1.0]

    oldest = 0.60
    newest = 1.00

    step = (newest - oldest) / (count - 1)

    return [
        oldest + (step * i)
        for i in range(count)
    ]


def weighted_average(values):
    if not values:
        return 0.0

    weights = recency_weights(len(values))

    numerator = sum(
        value * weight
        for value, weight in zip(values, weights)
    )

    denominator = sum(weights)

    if denominator == 0:
        return 0.0

    return numerator / denominator


def weighted_rate(values):
    return weighted_average(
        [1.0 if value else 0.0 for value in values]
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(matches):
    if not matches:
        return None

    wins = []
    draws = []
    losses = []

    gf_values = []
    ga_values = []
    over15 = []
    over25 = []
    under35 = []
    btts = []
    scoring = []
    clean_sheets = []

    for item in matches:
        gf = item["gf"]
        ga = item["ga"]

        wins.append(gf > ga)
        draws.append(gf == ga)
        losses.append(gf < ga)

        gf_values.append(gf)
        ga_values.append(ga)

        total = gf + ga

        over15.append(total >= 2)
        over25.append(total >= 3)
        under35.append(total <= 3)
        btts.append(gf > 0 and ga > 0)
        scoring.append(gf > 0)
        clean_sheets.append(ga == 0)

    return {
        "count": len(matches),
        "wins": sum(wins),
        "draws": sum(draws),
        "losses": sum(losses),

        "gf": sum(gf_values),
        "ga": sum(ga_values),

        "avg_gf": weighted_average(gf_values),
        "avg_ga": weighted_average(ga_values),

        "over15": weighted_rate(over15) * 100,
        "over25": weighted_rate(over25) * 100,
        "under35": weighted_rate(under35) * 100,
        "btts": weighted_rate(btts) * 100,
        "scoring": weighted_rate(scoring) * 100,
        "clean_sheet": weighted_rate(clean_sheets) * 100,
    }


def form_string(matches):
    chars = []

    for item in matches:
        if item["gf"] > item["ga"]:
            chars.append("W")
        elif item["gf"] == item["ga"]:
            chars.append("D")
        else:
            chars.append("L")

    return "".join(chars)


# ============================================================
# VENUE MATCHES
# ============================================================

def get_home_matches(matches):
    return [
        item for item in matches
        if item["is_home"]
    ]


def get_away_matches(matches):
    return [
        item for item in matches
        if not item["is_home"]
    ]


# ============================================================
# XG MODEL
# ============================================================

def blend(a, b, weight_a):
    return (
        a * weight_a
        + b * (1.0 - weight_a)
    )


def estimate_xg(
    team_stats,
    opponent_stats,
    venue_team,
    venue_opponent,
):
    """
    Estimated xG based on:
    - recent attack
    - opponent recent defence
    - venue attack
    - venue defensive record
    - recent form
    - conservative shrinkage
    """

    team_attack = team_stats["avg_gf"]
    opponent_defence = opponent_stats["avg_ga"]

    base_attack = (
        team_attack * 0.55
        + opponent_defence * 0.45
    )

    venue_attack = venue_team["avg_gf"]
    venue_defence = venue_opponent["avg_ga"]

    venue_component = (
        venue_attack * 0.55
        + venue_defence * 0.45
    )

    # Recent evidence remains dominant when venue sample is small.
    venue_count = venue_team["count"]

    if venue_count >= 5:
        venue_weight = 0.40
    elif venue_count == 4:
        venue_weight = 0.30
    elif venue_count == 3:
        venue_weight = 0.24
    elif venue_count == 2:
        venue_weight = 0.18
    elif venue_count == 1:
        venue_weight = 0.10
    else:
        venue_weight = 0.0

    xg = blend(
        base_attack,
        venue_component,
        venue_weight,
    )

    # Small shrinkage toward league-neutral scoring.
    xg = (
        xg * 0.88
        + 1.35 * 0.12
    )

    return max(
        0.15,
        min(xg, 4.00)
    )


# ============================================================
# POISSON
# ============================================================

def poisson_probability(lmbda, k):
    try:
        return (
            math.exp(-lmbda)
            * (lmbda ** k)
            / math.factorial(k)
        )
    except Exception:
        return 0.0


def poisson_matrix(home_xg, away_xg, max_goals=8):
    matrix = []

    total = 0.0

    for home_goals in range(max_goals + 1):
        row = []

        for away_goals in range(max_goals + 1):
            probability = (
                poisson_probability(home_xg, home_goals)
                * poisson_probability(away_xg, away_goals)
            )

            row.append(probability)
            total += probability

        matrix.append(row)

    # Normalize because we truncate the score range.
    if total > 0:
        for h in range(len(matrix)):
            for a in range(len(matrix[h])):
                matrix[h][a] /= total

    return matrix


def matrix_markets(matrix):
    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    over05 = 0.0
    over15 = 0.0
    over25 = 0.0

    under35 = 0.0
    under45 = 0.0

    btts = 0.0
    home_score = 0.0
    away_score = 0.0

    for h in range(len(matrix)):
        for a in range(len(matrix[h])):
            p = matrix[h][a]
            total = h + a

            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p

            if total >= 1:
                over05 += p

            if total >= 2:
                over15 += p

            if total >= 3:
                over25 += p

            if total <= 3:
                under35 += p

            if total <= 4:
                under45 += p

            if h > 0 and a > 0:
                btts += p

            if h > 0:
                home_score += p

            if a > 0:
                away_score += p

    return {
        "home_win": home_win * 100,
        "draw": draw * 100,
        "away_win": away_win * 100,

        "1x": (home_win + draw) * 100,
        "x2": (draw + away_win) * 100,

        "over05": over05 * 100,
        "over15": over15 * 100,
        "over25": over25 * 100,

        "under35": under35 * 100,
        "under45": under45 * 100,

        "btts": btts * 100,

        "home_score": home_score * 100,
        "away_score": away_score * 100,
    }


# ============================================================
# SCORELINES
# ============================================================

def top_scorelines(matrix, limit=3):
    scores = []

    for h in range(len(matrix)):
        for a in range(len(matrix[h])):
            scores.append(
                (
                    matrix[h][a] * 100,
                    h,
                    a,
                )
            )

    scores.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return scores[:limit]


def grouped_goal_distribution(matrix):
    zero_one = 0.0
    two_three = 0.0
    four = 0.0
    five_plus = 0.0

    for h in range(len(matrix)):
        for a in range(len(matrix[h])):
            p = matrix[h][a]
            total = h + a

            if total <= 1:
                zero_one += p
            elif total <= 3:
                two_three += p
            elif total == 4:
                four += p
            else:
                five_plus += p

    return {
        "zero_one": zero_one * 100,
        "two_three": two_three * 100,
        "four": four * 100,
        "five_plus": five_plus * 100,
    }


# ============================================================
# DATA STRENGTH
# ============================================================

def calculate_data_strength(
    home_recent_count,
    away_recent_count,
    home_venue_count,
    away_venue_count,
):
    recent_ratio = (
        min(home_recent_count, SAMPLE_SIZE)
        + min(away_recent_count, SAMPLE_SIZE)
    ) / (SAMPLE_SIZE * 2)

    venue_target = 5

    venue_ratio = (
        min(home_venue_count, venue_target)
        + min(away_venue_count, venue_target)
    ) / (venue_target * 2)

    strength = (
        recent_ratio * 0.65
        + venue_ratio * 0.35
    )

    return round(strength * 100)


# ============================================================
# MODEL DISAGREEMENT
# ============================================================

def calculate_disagreement(
    home_recent,
    away_recent,
    home_venue,
    away_venue,
):
    differences = []

    # Win-rate disagreement.
    home_recent_win_rate = (
        home_recent["wins"] / home_recent["count"]
        if home_recent["count"] else 0
    )

    home_venue_win_rate = (
        home_venue["wins"] / home_venue["count"]
        if home_venue["count"] else 0
    )

    away_recent_win_rate = (
        away_recent["wins"] / away_recent["count"]
        if away_recent["count"] else 0
    )

    away_venue_win_rate = (
        away_venue["wins"] / away_venue["count"]
        if away_venue["count"] else 0
    )

    differences.append(
        abs(
            home_recent_win_rate
            - home_venue_win_rate
        )
    )

    differences.append(
        abs(
            away_recent_win_rate
            - away_venue_win_rate
        )
    )

    # Scoring contradiction.
    differences.append(
        abs(
            home_recent["scoring"]
            - home_venue["scoring"]
        ) / 100
    )

    differences.append(
        abs(
            away_recent["scoring"]
            - away_venue["scoring"]
        ) / 100
    )

    highest = max(differences)

    if highest >= 0.60:
        return "HIGH"

    if highest >= 0.40:
        return "MEDIUM"

    return "LOW"


# ============================================================
# CONFIDENCE
# ============================================================

def calculate_model_confidence(
    data_strength,
    disagreement,
):
    """
    This is a reliability heuristic, not a calibrated
    probability of prediction correctness.
    """

    confidence = (
        50
        + (data_strength * 0.32)
    )

    if disagreement == "MEDIUM":
        confidence -= 5
    elif disagreement == "HIGH":
        confidence -= 10

    # Conservative ceiling.
    confidence = min(
        confidence,
        82
    )

    confidence = max(
        confidence,
        50
    )

    return round(confidence)


# ============================================================
# PROBABILITY AUDIT
# ============================================================

def probability_audit(markets):
    checks = []

    # 1X2 must equal 100.
    result_total = (
        markets["home_win"]
        + markets["draw"]
        + markets["away_win"]
    )

    checks.append(
        abs(result_total - 100) <= 0.2
    )

    # Goal totals must be monotonic.
    checks.append(
        markets["over05"]
        >= markets["over15"]
        >= markets["over25"]
    )

    checks.append(
        markets["under35"]
        <= markets["under45"]
    )

    # BTTS cannot exceed either team's scoring probability.
    checks.append(
        markets["btts"]
        <= markets["home_score"] + 0.2
    )

    checks.append(
        markets["btts"]
        <= markets["away_score"] + 0.2
    )

    return all(checks)


def xg_sanity_check(total_xg, over05):
    expected = (
        1
        - math.exp(-total_xg)
    ) * 100

    return abs(expected - over05) <= 1.0


# ============================================================
# FORMAT HELPERS
# ============================================================

def pct(value):
    return f"{round(value):.0f}%"


def format_stats(title, stats, matches):
    return (
        f"{title}:\n"
        f"{form_string(matches)} | "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']} | "
        f"GF{stats['gf']} GA{stats['ga']}\n"
        f"Avg {stats['avg_gf']:.2f}/{stats['avg_ga']:.2f} | "
        f"O1.5 {pct(stats['over15'])} | "
        f"O2.5 {pct(stats['over25'])} | "
        f"U3.5 {pct(stats['under35'])} | "
        f"BTTS {pct(stats['btts'])}\n"
        f"Scoring {pct(stats['scoring'])} | "
        f"CS {pct(stats['clean_sheet'])}"
    )


def format_venue(title, stats):
    return (
        f"{title}:\n"
        f"{stats['count']} games | "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']} | "
        f"Avg {stats['avg_gf']:.2f}/{stats['avg_ga']:.2f}\n"
        f"Scoring {pct(stats['scoring'])} | "
        f"CS {pct(stats['clean_sheet'])}"
    )


# ============================================================
# ANALYSIS
# ============================================================

def analyze_match(
    home_team,
    away_team,
    home_matches,
    away_matches,
):
    home_recent = home_matches[:SAMPLE_SIZE]
    away_recent = away_matches[:SAMPLE_SIZE]

    home_stats = calculate_stats(home_recent)
    away_stats = calculate_stats(away_recent)

    if not home_stats or not away_stats:
        return None, "Not enough completed data."

    home_venue_matches = get_home_matches(home_recent)
    away_venue_matches = get_away_matches(away_recent)

    home_venue = calculate_stats(home_venue_matches)

    if not home_venue:
        home_venue = {
            "count": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "avg_gf": home_stats["avg_gf"],
            "avg_ga": home_stats["avg_ga"],
            "scoring": home_stats["scoring"],
            "clean_sheet": home_stats["clean_sheet"],
        }

    away_venue = calculate_stats(away_venue_matches)

    if not away_venue:
        away_venue = {
            "count": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "avg_gf": away_stats["avg_gf"],
            "avg_ga": away_stats["avg_ga"],
            "scoring": away_stats["scoring"],
            "clean_sheet": away_stats["clean_sheet"],
        }

    home_xg = estimate_xg(
        home_stats,
        away_stats,
        home_venue,
        away_venue,
    )

    away_xg = estimate_xg(
        away_stats,
        home_stats,
        away_venue,
        home_venue,
    )

    total_xg = home_xg + away_xg

    matrix = poisson_matrix(
        home_xg,
        away_xg,
        max_goals=8,
    )

    markets = matrix_markets(matrix)

    scores = top_scorelines(
        matrix,
        limit=3,
    )

    goals = grouped_goal_distribution(matrix)

    data_strength = calculate_data_strength(
        len(home_recent),
        len(away_recent),
        len(home_venue_matches),
        len(away_venue_matches),
    )

    disagreement = calculate_disagreement(
        home_stats,
        away_stats,
        home_venue,
        away_venue,
    )

    confidence = calculate_model_confidence(
        data_strength,
        disagreement,
    )

    consistency = probability_audit(markets)

    xg_check = xg_sanity_check(
        total_xg,
        markets["over05"],
    )

    signals = [
        ("Over 0.5", markets["over05"]),
        ("Over 1.5", markets["over15"]),
        ("Over 2.5", markets["over25"]),
        ("Under 3.5", markets["under35"]),
        ("Under 4.5", markets["under45"]),
        ("BTTS", markets["btts"]),
        ("Team 1 to score", markets["home_score"]),
        ("Team 2 to score", markets["away_score"]),
        ("1X", markets["1x"]),
        ("X2", markets["x2"]),
    ]

    signals.sort(
        key=lambda x: x[1],
        reverse=True,
    )

    top_signals = signals[:3]

    lines = []

    lines.append(
        f"⚽ GOALLOGIC AI v{VERSION}"
    )

    lines.append("")
    lines.append(
        f"🏟️ {team_name(home_team)} vs "
        f"{team_name(away_team)}"
    )

    lines.append("")
    lines.append(
        f"Season: {SEASON}"
    )

    lines.append(
        f"Sample: Last {SAMPLE_SIZE} available matches"
    )

    lines.append("")

    lines.append(
        format_stats(
            f"🏠 {team_name(home_team)} recent",
            home_stats,
            home_recent,
        )
    )

    lines.append("")

    lines.append(
        format_stats(
            f"✈️ {team_name(away_team)} recent",
            away_stats,
            away_recent,
        )
    )

    lines.append("")

    lines.append(
        format_venue(
            f"🏟️ {team_name(home_team)} HOME",
            home_venue,
        )
    )

    lines.append("")

    lines.append(
        format_venue(
            f"✈️ {team_name(away_team)} AWAY",
            away_venue,
        )
    )

    lines.append("")

    lines.append("📊 EXPECTED GOALS MODEL")

    lines.append(
        f"{team_name(home_team)}: {home_xg:.2f} xG"
    )

    lines.append(
        f"{team_name(away_team)}: {away_xg:.2f} xG"
    )

    lines.append(
        f"Total: {total_xg:.2f} xG"
    )

    lines.append("")

    lines.append("🎯 RESULT MODEL")

    lines.append(
        f"1: {pct(markets['home_win'])} | "
        f"X: {pct(markets['draw'])} | "
        f"2: {pct(markets['away_win'])}"
    )

    lines.append("")

    lines.append("📈 MARKET MODEL")

    lines.append(f"1X: {pct(markets['1x'])}")
    lines.append(f"X2: {pct(markets['x2'])}")
    lines.append(f"Over 0.5: {pct(markets['over05'])}")
    lines.append(f"Over 1.5: {pct(markets['over15'])}")
    lines.append(f"Over 2.5: {pct(markets['over25'])}")
    lines.append(f"Under 3.5: {pct(markets['under35'])}")
    lines.append(f"Under 4.5: {pct(markets['under45'])}")
    lines.append(f"BTTS: {pct(markets['btts'])}")
    lines.append(
        f"Team 1 to score: {pct(markets['home_score'])}"
    )
    lines.append(
        f"Team 2 to score: {pct(markets['away_score'])}"
    )

    lines.append("")

    lines.append("🥅 TOP SCORELINES")

    for probability, h, a in scores:
        lines.append(
            f"{h}-{a}: {pct(probability)}"
        )

    lines.append("")

    lines.append("⚽ GOAL DISTRIBUTION")

    lines.append(
        f"0-1: {pct(goals['zero_one'])} | "
        f"2-3: {pct(goals['two_three'])} | "
        f"4: {pct(goals['four'])} | "
        f"5+: {pct(goals['five_plus'])}"
    )

    lines.append(
        "0-1 / 2-3 / 4 / 5+ goals"
    )

    lines.append("")

    lines.append("🔥 TOP MODEL SIGNALS")

    for index, (name, probability) in enumerate(
        top_signals
    ):
        emoji = "🟢" if probability >= 65 else "🟡"

        lines.append(
            f"{emoji} {name} {pct(probability)}"
        )

    lines.append("")

    lines.append("🧠 MODEL QUALITY")

    lines.append(
        f"Data strength: {data_strength}%"
    )

    lines.append(
        "Probability consistency: "
        + ("✅ PASS" if consistency else "⚠️ CHECK")
    )

    lines.append(
        "xG/market sanity check: "
        + ("✅ PASS" if xg_check else "⚠️ CHECK")
    )

    lines.append(
        f"Model disagreement: {disagreement}"
    )

    if confidence >= 75:
        confidence_label = "🟢 HIGH"
    elif confidence >= 65:
        confidence_label = "🟡 MODERATE"
    else:
        confidence_label = "🔴 LOW"

    lines.append(
        f"Model confidence: {confidence_label} "
        f"{confidence}%"
    )

    lines.append("")

    primary_name, primary_probability = top_signals[0]

    lines.append(
        f"📌 PRIMARY MODEL SIGNAL: "
        f"{primary_name} {pct(primary_probability)}"
    )

    lines.append("")

    lines.append(
        "ℹ️ Probabilities are mathematical model estimates "
        "from recent data, venue evidence and a Poisson "
        "score model. They are not guarantees."
    )

    lines.append(
        "ℹ️ Model confidence measures data/model reliability; "
        "it is not the probability that a prediction will be correct."
    )

    return "\n".join(lines), None


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        f"⚽ Welcome to GoalLogic AI v{VERSION}!\n\n"
        "I analyze football matches using recent form, "
        "home/away evidence, weighted statistics, "
        "expected goals and a Poisson score model.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )

    await update.message.reply_text(message)


async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    teams, error = search_team("Chelsea")

    if error:
        await update.message.reply_text(
            "🔧 FOOTBALL API TEST\n\n"
            f"❌ {error}"
        )
        return

    if not teams:
        await update.message.reply_text(
            "🔧 FOOTBALL API TEST\n\n"
            "❌ Chelsea was not found."
        )
        return

    team = teams[0]

    await update.message.reply_text(
        "🔧 FOOTBALL API TEST\n\n"
        "OpenFoot API connection works.\n"
        f"Found {team_name(team)}.\n"
        f"Team ID: {team_id(team)}"
    )


async def team_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = " ".join(context.args).strip()

    if not query:
        await update.message.reply_text(
            "Usage: /team Chelsea"
        )
        return

    team, error = find_team(query)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    lines = [
        "🔎 TEAM SEARCH",
        "",
        f"Name: {team_name(team)}",
        f"ID: {team_id(team)}",
    ]

    country = (
        team.get("country")
        if isinstance(team, dict)
        else None
    )

    if country:
        lines.append(
            f"Country: {country}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def fixtures_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = " ".join(context.args).strip()

    if not query:
        await update.message.reply_text(
            "Usage: /fixtures Chelsea"
        )
        return

    team, error = find_team(query)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    matches, error = get_team_matches(
        team_id(team)
    )

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    oriented = team_oriented_matches(
        matches,
        team_id(team),
        team_name(team),
    )

    recent = oriented[:SAMPLE_SIZE]

    if not recent:
        await update.message.reply_text(
            f"❌ No completed {SEASON} matches found "
            f"for {team_name(team)}."
        )
        return

    lines = [
        f"📅 {team_name(team)}",
        "",
        f"Season: {SEASON}",
        "",
        f"Showing last {len(recent)} completed matches",
    ]

    for item in recent:
        result = (
            "W"
            if item["gf"] > item["ga"]
            else "D"
            if item["gf"] == item["ga"]
            else "L"
        )

        venue = (
            "HOME"
            if item["is_home"]
            else "AWAY"
        )

        score = (
            f"{item['gf']}-{item['ga']}"
        )

        lines.append(
            f"{item['date'][:10]} | "
            f"{venue} | "
            f"{result} | "
            f"{score} vs {item['opponent']}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = " ".join(context.args).strip()

    if " vs " not in text.lower():
        await update.message.reply_text(
            "Usage:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    parts = text.lower().split(" vs ", 1)

    if len(parts) != 2:
        await update.message.reply_text(
            "Usage:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    home_query = parts[0].strip()
    away_query = parts[1].strip()

    if not home_query or not away_query:
        await update.message.reply_text(
            "Usage:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    home_team, error = find_team(home_query)

    if error:
        await update.message.reply_text(
            f"❌ Home team error: {error}"
        )
        return

    away_team, error = find_team(away_query)

    if error:
        await update.message.reply_text(
            f"❌ Away team error: {error}"
        )
        return

    home_matches_raw, error = get_team_matches(
        team_id(home_team)
    )

    if error:
        await update.message.reply_text(
            f"❌ {team_name(home_team)}: {error}"
        )
        return

    away_matches_raw, error = get_team_matches(
        team_id(away_team)
    )

    if error:
        await update.message.reply_text(
            f"❌ {team_name(away_team)}: {error}"
        )
        return

    home_matches = team_oriented_matches(
        home_matches_raw,
        team_id(home_team),
        team_name(home_team),
    )

    away_matches = team_oriented_matches(
        away_matches_raw,
        team_id(away_team),
        team_name(away_team),
    )

    if len(home_matches) < 1:
        await update.message.reply_text(
            f"❌ No recent {SEASON} data found for "
            f"{team_name(home_team)}."
        )
        return

    if len(away_matches) < 1:
        await update.message.reply_text(
            f"❌ No recent {SEASON} data found for "
            f"{team_name(away_team)}."
        )
        return

    result, error = analyze_match(
        home_team,
        away_team,
        home_matches,
        away_matches,
    )

    if error:
        await update.message.reply_text(
            f"❌ Analysis error: {error}"
        )
        return

    await update.message.reply_text(
        result
    )


# ============================================================
# MAIN
# ============================================================

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    print(
        f"GoalLogic AI v{VERSION} Telegram bot is starting."
    )

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("apitest", apitest_command)
    )

    application.add_handler(
        CommandHandler("team", team_command)
    )

    application.add_handler(
        CommandHandler("fixtures", fixtures_command)
    )

    application.add_handler(
        CommandHandler("analyze", analyze_command)
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
