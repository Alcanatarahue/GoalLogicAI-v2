import os
import math
import threading
import http.server
from datetime import datetime

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# GOALLOGIC AI v2.9
# ============================================================

VERSION = "2.9"

OPENFOOT_API_KEY = os.environ.get("OPENFOOT_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

API_BASE = "https://openfootapi.com/v1"

SEASON = "2026/27"
SAMPLE_SIZE = 5

PORT = int(os.environ.get("PORT", "10000"))


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(http.server.BaseHTTPRequestHandler):

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

    server = http.server.HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"GoalLogic AI v{VERSION} is running on port {PORT}."
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()


# ============================================================
# OPENFOOT API
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
            timeout=30
        )

        if response.status_code != 200:

            try:
                payload = response.json()

                error = payload.get("error", {})

                if isinstance(error, dict):
                    message = error.get(
                        "message",
                        f"HTTP {response.status_code}"
                    )
                else:
                    message = str(error)

            except Exception:

                message = f"HTTP {response.status_code}"

            return None, message

        payload = response.json()

        return payload, None

    except Exception as exc:

        return None, str(exc)


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_name(name):

    return " ".join(
        str(name or "").strip().lower().split()
    )


def team_name(team):

    if not isinstance(team, dict):
        return ""

    return str(
        team.get("name")
        or team.get("shortName")
        or team.get("displayName")
        or ""
    )


def team_id(team):

    if not isinstance(team, dict):
        return ""

    return str(
        team.get("id")
        or team.get("teamId")
        or ""
    )


def safe_number(value):

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value) if value.is_integer() else None

    if isinstance(value, str):

        value = value.strip()

        if value.isdigit():
            return int(value)

    return None


# ============================================================
# TEAM SEARCH
# ============================================================

def search_team(query):

    payload, error = api_get(
        "/search",
        {"q": query}
    )

    if error:
        return [], error

    if not payload:
        return [], "Empty API response."

    data = payload.get("data", [])

    if isinstance(data, dict):

        data = (
            data.get("teams")
            or data.get("results")
            or []
        )

    if not isinstance(data, list):

        return [], "Unexpected search response."

    return data, None


def find_team(query):

    teams, error = search_team(query)

    if error:
        return None, error

    if not teams:

        return None, (
            f"No team found for {query}."
        )

    wanted = clean_name(query)

    # Exact match
    for team in teams:

        if clean_name(team_name(team)) == wanted:
            return team, None

    # Contains match
    for team in teams:

        name = clean_name(team_name(team))

        if wanted in name or name in wanted:
            return team, None

    return teams[0], None


# ============================================================
# SCORE EXTRACTION
# ============================================================

def extract_scores(match):

    if not isinstance(match, dict):
        return None, None

    # Direct fields
    pairs = [
        ("homeScore", "awayScore"),
        ("homeGoals", "awayGoals"),
        ("home_score", "away_score"),
        ("home_goals", "away_goals"),
    ]

    for home_key, away_key in pairs:

        home_score = safe_number(
            match.get(home_key)
        )

        away_score = safe_number(
            match.get(away_key)
        )

        if (
            home_score is not None
            and away_score is not None
        ):
            return home_score, away_score

    # Nested score object
    score = match.get("score")

    if isinstance(score, dict):

        pairs = [
            ("home", "away"),
            ("homeScore", "awayScore"),
            ("homeGoals", "awayGoals"),
            ("home_score", "away_score"),
            ("home_goals", "away_goals"),
        ]

        for home_key, away_key in pairs:

            home_score = safe_number(
                score.get(home_key)
            )

            away_score = safe_number(
                score.get(away_key)
            )

            if (
                home_score is not None
                and away_score is not None
            ):
                return home_score, away_score

        home_obj = score.get("home")
        away_obj = score.get("away")

        if (
            isinstance(home_obj, dict)
            and isinstance(away_obj, dict)
        ):

            for key in [
                "score",
                "goals",
                "current",
                "fullTime",
            ]:

                home_score = safe_number(
                    home_obj.get(key)
                )

                away_score = safe_number(
                    away_obj.get(key)
                )

                if (
                    home_score is not None
                    and away_score is not None
                ):
                    return home_score, away_score

    # Scores object
    scores = match.get("scores")

    if isinstance(scores, dict):

        pairs = [
            ("home", "away"),
            ("homeScore", "awayScore"),
            ("homeGoals", "awayGoals"),
            ("home_score", "away_score"),
        ]

        for home_key, away_key in pairs:

            home_score = safe_number(
                scores.get(home_key)
            )

            away_score = safe_number(
                scores.get(away_key)
            )

            if (
                home_score is not None
                and away_score is not None
            ):
                return home_score, away_score

    # Team objects
    home_team = match.get("homeTeam", {})
    away_team = match.get("awayTeam", {})

    if (
        isinstance(home_team, dict)
        and isinstance(away_team, dict)
    ):

        for key in [
            "score",
            "goals",
        ]:

            home_score = safe_number(
                home_team.get(key)
            )

            away_score = safe_number(
                away_team.get(key)
            )

            if (
                home_score is not None
                and away_score is not None
            ):
                return home_score, away_score

    # Result object
    result = match.get("result")

    if isinstance(result, dict):

        for home_key, away_key in [
            ("home", "away"),
            ("homeScore", "awayScore"),
            ("homeGoals", "awayGoals"),
        ]:

            home_score = safe_number(
                result.get(home_key)
            )

            away_score = safe_number(
                result.get(away_key)
            )

            if (
                home_score is not None
                and away_score is not None
            ):
                return home_score, away_score

    return None, None


# ============================================================
# MATCH HELPERS
# ============================================================

def get_status(match):

    if not isinstance(match, dict):
        return ""

    return str(
        match.get("status")
        or match.get("matchStatus")
        or ""
    ).strip().lower()


def match_datetime(match):

    if not isinstance(match, dict):
        return None

    raw = (
        match.get("kickoffAt")
        or match.get("date")
        or match.get("startTime")
        or match.get("utcDate")
    )

    if not raw:
        return None

    try:

        return datetime.fromisoformat(
            str(raw).replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:

        return None


# ============================================================
# GET TEAM MATCHES
# ============================================================

def get_team_matches(team_id_value):

    payload, error = api_get(
        "/matches",
        {
            "team": team_id_value,
            "season": SEASON,
        }
    )

    if error:
        return [], error

    if not payload:
        return [], "Empty matches response."

    data = payload.get("data", [])

    if isinstance(data, dict):

        data = (
            data.get("matches")
            or data.get("results")
            or data.get("fixtures")
            or []
        )

    if not isinstance(data, list):

        return [], "Unexpected matches response."

    completed = []

    for match in data:

        if not isinstance(match, dict):
            continue

        home_team = match.get(
            "homeTeam",
            {}
        )

        away_team = match.get(
            "awayTeam",
            {}
        )

        home_score, away_score = extract_scores(
            match
        )

        if (
            home_score is None
            or away_score is None
        ):
            continue

        status = get_status(match)

        if status in {
            "scheduled",
            "upcoming",
            "live",
            "inplay",
            "postponed",
            "cancelled",
            "canceled",
        }:
            continue

        completed.append({
            "id": match.get("id"),
            "status": status or "finished",
            "kickoffAt": (
                match.get("kickoffAt")
                or match.get("date")
                or match.get("startTime")
                or match.get("utcDate")
            ),
            "homeTeam": home_team,
            "awayTeam": away_team,
            "_home_score": home_score,
            "_away_score": away_score,
        })

    completed.sort(
        key=lambda item: (
            match_datetime(item)
            or datetime.min
        ),
        reverse=True
    )

    return completed, None


# ============================================================
# TEAM-ORIENTED MATCHES
# ============================================================

def team_oriented_matches(team, matches):

    target_id = team_id(team)
    target_name = clean_name(
        team_name(team)
    )

    result = []

    for match in matches:

        home = match.get(
            "homeTeam",
            {}
        )

        away = match.get(
            "awayTeam",
            {}
        )

        home_id = team_id(home)
        away_id = team_id(away)

        if target_id and home_id == target_id:

            is_home = True

        elif target_id and away_id == target_id:

            is_home = False

        else:

            home_name = clean_name(
                team_name(home)
            )

            away_name = clean_name(
                team_name(away)
            )

            if target_name == home_name:

                is_home = True

            elif target_name == away_name:

                is_home = False

            else:

                continue

        home_score = match.get(
            "_home_score"
        )

        away_score = match.get(
            "_away_score"
        )

        if (
            home_score is None
            or away_score is None
        ):
            continue

        if is_home:

            gf = home_score
            ga = away_score
            venue = "HOME"
            opponent = team_name(away)

        else:

            gf = away_score
            ga = home_score
            venue = "AWAY"
            opponent = team_name(home)

        if gf > ga:
            result_code = "W"

        elif gf == ga:
            result_code = "D"

        else:
            result_code = "L"

        result.append({
            "date": match.get("kickoffAt"),
            "venue": venue,
            "opponent": opponent,
            "gf": gf,
            "ga": ga,
            "result": result_code,
        })

    result.sort(
        key=lambda item: (
            match_datetime(
                {
                    "kickoffAt": item.get("date")
                }
            )
            or datetime.min
        ),
        reverse=True
    )

    return result


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(matches):

    if not matches:

        return {
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "avg_gf": 0.0,
            "avg_ga": 0.0,
            "over15": 0.0,
            "over25": 0.0,
            "under35": 0.0,
            "btts": 0.0,
            "scoring": 0.0,
            "clean_sheet": 0.0,
        }

    games = len(matches)

    wins = sum(
        1 for m in matches
        if m["result"] == "W"
    )

    draws = sum(
        1 for m in matches
        if m["result"] == "D"
    )

    losses = sum(
        1 for m in matches
        if m["result"] == "L"
    )

    gf = sum(
        m["gf"] for m in matches
    )

    ga = sum(
        m["ga"] for m in matches
    )

    over15 = sum(
        1 for m in matches
        if m["gf"] + m["ga"] >= 2
    )

    over25 = sum(
        1 for m in matches
        if m["gf"] + m["ga"] >= 3
    )

    under35 = sum(
        1 for m in matches
        if m["gf"] + m["ga"] <= 3
    )

    btts = sum(
        1 for m in matches
        if m["gf"] > 0
        and m["ga"] > 0
    )

    scoring = sum(
        1 for m in matches
        if m["gf"] > 0
    )

    clean_sheet = sum(
        1 for m in matches
        if m["ga"] == 0
    )

    return {
        "games": games,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf,
        "ga": ga,
        "avg_gf": gf / games,
        "avg_ga": ga / games,
        "over15": over15 / games * 100,
        "over25": over25 / games * 100,
        "under35": under35 / games * 100,
        "btts": btts / games * 100,
        "scoring": scoring / games * 100,
        "clean_sheet": clean_sheet / games * 100,
    }


# ============================================================
# POISSON
# ============================================================

def poisson_probability(lam, goals):

    if lam <= 0:
        return 0.0

    return (
        math.exp(-lam)
        * (lam ** goals)
        / math.factorial(goals)
    )


def poisson_matrix(
    home_xg,
    away_xg,
    max_goals=10
):

    matrix = {}

    for home_goals in range(
        max_goals + 1
    ):

        for away_goals in range(
            max_goals + 1
        ):

            probability = (
                poisson_probability(
                    home_xg,
                    home_goals
                )
                *
                poisson_probability(
                    away_xg,
                    away_goals
                )
            )

            matrix[
                (home_goals, away_goals)
            ] = probability

    total = sum(
        matrix.values()
    )

    if total > 0:

        matrix = {
            key: value / total
            for key, value in matrix.items()
        }

    return matrix


def poisson_markets(
    home_xg,
    away_xg
):

    matrix = poisson_matrix(
        home_xg,
        away_xg
    )

    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    over15 = 0.0
    over25 = 0.0
    under35 = 0.0
    under45 = 0.0
    over05 = 0.0
    btts = 0.0

    home_score = 0.0
    away_score = 0.0

    for (
        home_goals,
        away_goals
    ), probability in matrix.items():

        total_goals = (
            home_goals
            + away_goals
        )

        if home_goals > away_goals:

            home_win += probability

        elif home_goals == away_goals:

            draw += probability

        else:

            away_win += probability

        if total_goals >= 1:
            over05 += probability

        if total_goals >= 2:
            over15 += probability

        if total_goals >= 3:
            over25 += probability

        if total_goals <= 3:
            under35 += probability

        if total_goals <= 4:
            under45 += probability

        if (
            home_goals > 0
            and away_goals > 0
        ):
            btts += probability

        if home_goals > 0:
            home_score += probability

        if away_goals > 0:
            away_score += probability

    return {
        "home_win": home_win * 100,
        "draw": draw * 100,
        "away_win": away_win * 100,

        "1x": (
            home_win + draw
        ) * 100,

        "x2": (
            draw + away_win
        ) * 100,

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

def top_scorelines(
    home_xg,
    away_xg,
    count=5
):

    matrix = poisson_matrix(
        home_xg,
        away_xg,
        max_goals=8
    )

    ranked = sorted(
        matrix.items(),
        key=lambda item: item[1],
        reverse=True
    )

    return ranked[:count]


# ============================================================
# xG ESTIMATION
# ============================================================

def calculate_xg(
    home_recent,
    away_recent,
    home_venue,
    away_venue
):

    home_attack = home_recent["avg_gf"]
    home_defense = home_recent["avg_ga"]

    away_attack = away_recent["avg_gf"]
    away_defense = away_recent["avg_ga"]

    home_venue_attack = (
        home_venue["avg_gf"]
        if home_venue["games"] > 0
        else home_attack
    )

    home_venue_defense = (
        home_venue["avg_ga"]
        if home_venue["games"] > 0
        else home_defense
    )

    away_venue_attack = (
        away_venue["avg_gf"]
        if away_venue["games"] > 0
        else away_attack
    )

    away_venue_defense = (
        away_venue["avg_ga"]
        if away_venue["games"] > 0
        else away_defense
    )

    home_venue_weight = (
        0.10
        if home_venue["games"] <= 1
        else 0.18
        if home_venue["games"] == 2
        else 0.22
    )

    away_venue_weight = (
        0.10
        if away_venue["games"] <= 1
        else 0.18
        if away_venue["games"] == 2
        else 0.22
    )

    home_recent_weight = (
        0.40
        if home_venue_weight <= 0.10
        else 0.36
        if home_venue_weight <= 0.18
        else 0.34
    )

    away_recent_weight = (
        0.40
        if away_venue_weight <= 0.10
        else 0.36
        if away_venue_weight <= 0.18
        else 0.34
    )

    home_remaining = (
        1
        - home_recent_weight
        - 0.30
        - home_venue_weight
    )

    away_remaining = (
        1
        - away_recent_weight
        - 0.30
        - away_venue_weight
    )

    home_xg = (
        home_attack * home_recent_weight
        + away_defense * 0.30
        + home_venue_attack * home_venue_weight
        + away_venue_defense * home_remaining
    )

    away_xg = (
        away_attack * away_recent_weight
        + home_defense * 0.30
        + away_venue_attack * away_venue_weight
        + home_venue_defense * away_remaining
    )

    home_xg = max(
        0.20,
        min(3.50, home_xg)
    )

    away_xg = max(
        0.20,
        min(3.50, away_xg)
    )

    return home_xg, away_xg


# ============================================================
# HISTORICAL MODEL
# ============================================================

def historical_markets(
    home_stats,
    away_stats,
    home_venue,
    away_venue
):

    home_unbeaten = (
        (
            home_stats["wins"]
            + home_stats["draws"]
        )
        / max(1, home_stats["games"])
        * 100
    )

    away_unbeaten = (
        (
            away_stats["wins"]
            + away_stats["draws"]
        )
        / max(1, away_stats["games"])
        * 100
    )

    home_win_rate = (
        home_stats["wins"]
        / max(1, home_stats["games"])
        * 100
    )

    away_win_rate = (
        away_stats["wins"]
        / max(1, away_stats["games"])
        * 100
    )

    home_venue_unbeaten = (
        (
            home_venue["wins"]
            + home_venue["draws"]
        )
        / max(1, home_venue["games"])
        * 100
    )

    away_venue_unbeaten = (
        (
            away_venue["wins"]
            + away_venue["draws"]
        )
        / max(1, away_venue["games"])
        * 100
    )

    return {

        "1x": (
            home_unbeaten * 0.70
            + home_venue_unbeaten * 0.30
        ),

        "x2": (
            away_unbeaten * 0.70
            + away_venue_unbeaten * 0.30
        ),

        "over05": 100.0,

        "over15": (
            home_stats["over15"]
            + away_stats["over15"]
        ) / 2,

        "over25": (
            home_stats["over25"]
            + away_stats["over25"]
        ) / 2,

        "under35": (
            home_stats["under35"]
            + away_stats["under35"]
        ) / 2,

        "under45": 100.0,

        "btts": (
            home_stats["btts"]
            + away_stats["btts"]
        ) / 2,

        "home_score": (
            home_stats["scoring"] * 0.60
            + home_venue["scoring"] * 0.40
        ),

        "away_score": (
            away_stats["scoring"] * 0.60
            + away_venue["scoring"] * 0.40
        ),

        "home_win": home_win_rate,

        "draw": 50.0,

        "away_win": away_win_rate,
    }


# ============================================================
# SAMPLE STRENGTH
# ============================================================

def sample_strength(
    home_games,
    away_games,
    home_venue_games,
    away_venue_games
):

    recent_games = min(
        home_games,
        away_games
    )

    venue_games = min(
        home_venue_games,
        away_venue_games
    )

    recent_score = min(
        1.0,
        recent_games / 5
    )

    venue_score = min(
        1.0,
        venue_games / 5
    )

    strength = (
        recent_score * 0.70
        + venue_score * 0.30
    )

    return max(
        0.20,
        min(1.0, strength)
    )


def shrink_probability(
    value,
    strength
):

    return (
        50
        + (value - 50) * strength
    )


def blend_market(
    poisson_value,
    historical_value,
    strength
):

    raw = (
        poisson_value * 0.80
        + historical_value * 0.20
    )

    final_value = shrink_probability(
        raw,
        strength
    )

    return max(
        1.0,
        min(99.0, final_value)
    )


def build_markets(
    poisson,
    historical,
    strength
):

    markets = {}

    keys = [
        "1x",
        "x2",
        "over05",
        "over15",
        "over25",
        "under35",
        "under45",
        "btts",
        "home_score",
        "away_score",
    ]

    for key in keys:

        markets[key] = blend_market(
            poisson[key],
            historical[key],
            strength
        )

    markets["home_win"] = blend_market(
        poisson["home_win"],
        historical["home_win"],
        strength
    )

    markets["away_win"] = blend_market(
        poisson["away_win"],
        historical["away_win"],
        strength
    )

    markets["draw"] = poisson["draw"]

    return markets


# ============================================================
# MODEL QUALITY
# ============================================================

def disagreement_level(
    poisson_value,
    historical_value
):

    difference = abs(
        poisson_value
        - historical_value
    )

    if difference >= 25:
        return "HIGH"

    if difference >= 15:
        return "MODERATE"

    return "LOW"


def model_confidence(
    signal_value,
    strength,
    disagreement
):

    signal_component = max(
        0,
        min(
            100,
            (signal_value - 50) * 2
        )
    )

    strength_component = (
        strength * 100
    )

    if disagreement == "HIGH":
        penalty = 20

    elif disagreement == "MODERATE":
        penalty = 10

    else:
        penalty = 0

    confidence = (
        signal_component * 0.55
        + strength_component * 0.45
        - penalty
    )

    return max(
        0,
        min(100, confidence)
    )


def confidence_label(value):

    if value >= 75:
        return "HIGH"

    if value >= 60:
        return "MEDIUM"

    return "LOW"


def confidence_icon(value):

    if value >= 75:
        return "🟢"

    if value >= 60:
        return "🟡"

    return "🔴"


# ============================================================
# DISPLAY
# ============================================================

def market_label(key):

    labels = {
        "1x": "1X",
        "x2": "X2",
        "over05": "Over 0.5",
        "over15": "Over 1.5",
        "over25": "Over 2.5",
        "under35": "Under 3.5",
        "under45": "Under 4.5",
        "btts": "BTTS",
        "home_score": "Team 1 to score",
        "away_score": "Team 2 to score",
        "home_win": "Team 1 win",
        "draw": "Draw",
        "away_win": "Team 2 win",
    }

    return labels.get(
        key,
        key
    )


def result_string(matches):

    return "".join(
        m["result"]
        for m in matches
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        f"⚽ Welcome to GoalLogic AI v{VERSION}!\n\n"
        "I analyze football matches using recent form, "
        "home/away evidence, expected goals and a "
        "Poisson score model.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )

    await update.message.reply_text(text)


# ============================================================
# /APITEST
# ============================================================

async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    teams, error = search_team("Chelsea")

    if error:

        await update.message.reply_text(
            "🔧 FOOTBALL API TEST\n\n"
            f"❌ API error:\n{error}"
        )

        return

    if not teams:

        await update.message.reply_text(
            "🔧 FOOTBALL API TEST\n\n"
            "❌ OpenFoot API connected, "
            "but Chelsea was not found."
        )

        return

    team = teams[0]

    await update.message.reply_text(
        "🔧 FOOTBALL API TEST\n\n"
        "OpenFoot API connection works.\n"
        "Found Chelsea.\n"
        f"Team ID: {team_id(team)}"
    )


# ============================================================
# /TEAM
# ============================================================

async def team_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "Usage:\n/team Chelsea"
        )

        return

    query = " ".join(
        context.args
    )

    teams, error = search_team(query)

    if error:

        await update.message.reply_text(
            f"❌ Team search error:\n{error}"
        )

        return

    if not teams:

        await update.message.reply_text(
            f"❌ No teams found for: {query}"
        )

        return

    lines = [
        f"🔎 Teams found for: {query}",
        ""
    ]

    for team in teams[:8]:

        lines.append(
            f"• {team_name(team)}\n"
            f"  ID: {team_id(team)}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# /FIXTURES
# ============================================================

async def fixtures_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "Usage:\n/fixtures Chelsea"
        )

        return

    query = " ".join(
        context.args
    )

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
            "❌ Could not retrieve matches:\n"
            f"{error}"
        )

        return

    oriented = team_oriented_matches(
        team,
        matches
    )

    if not oriented:

        await update.message.reply_text(
            f"❌ No completed {SEASON} matches "
            f"found for {team_name(team)}."
        )

        return

    sample = oriented[:SAMPLE_SIZE]

    lines = [
        f"📅 {team_name(team)}",
        f"Season: {SEASON}",
        f"Showing last {len(sample)} "
        "completed matches",
        ""
    ]

    for match in sample:

        date_text = str(
            match.get("date") or ""
        )[:10]

        lines.append(
            f"{date_text} | "
            f"{match['venue']} | "
            f"{match['result']} | "
            f"{match['gf']}-{match['ga']} "
            f"vs {match['opponent']}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# /ANALYZE
# ============================================================

async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if len(context.args) < 3:

        await update.message.reply_text(
            "Usage:\n"
            "/analyze Chelsea vs Arsenal"
        )

        return

    raw = " ".join(
        context.args
    )

    parts = raw.split(
        " vs ",
        1
    )

    if len(parts) != 2:

        parts = raw.split(
            " VS ",
            1
        )

    if len(parts) != 2:

        await update.message.reply_text(
            "❌ Use this format:\n"
            "/analyze Chelsea vs Arsenal"
        )

        return

    home_query = parts[0].strip()
    away_query = parts[1].strip()

    if (
        not home_query
        or not away_query
    ):

        await update.message.reply_text(
            "❌ Please provide both teams."
        )

        return

    # --------------------------------------------------------
    # FIND TEAMS
    # --------------------------------------------------------

    home_team, error = find_team(
        home_query
    )

    if error:

        await update.message.reply_text(
            f"❌ {error}"
        )

        return

    away_team, error = find_team(
        away_query
    )

    if error:

        await update.message.reply_text(
            f"❌ {error}"
        )

        return

    # --------------------------------------------------------
    # GET MATCHES
    # --------------------------------------------------------

    home_matches_raw, error = get_team_matches(
        team_id(home_team)
    )

    if error:

        await update.message.reply_text(
            f"❌ Could not retrieve data for "
            f"{team_name(home_team)}:\n"
            f"{error}"
        )

        return

    away_matches_raw, error = get_team_matches(
        team_id(away_team)
    )

    if error:

        await update.message.reply_text(
            f"❌ Could not retrieve data for "
            f"{team_name(away_team)}:\n"
            f"{error}"
        )

        return

    home_matches = team_oriented_matches(
        home_team,
        home_matches_raw
    )

    away_matches = team_oriented_matches(
        away_team,
        away_matches_raw
    )

    if not home_matches:

        await update.message.reply_text(
            f"❌ No recent {SEASON} data found "
            f"for {team_name(home_team)}."
        )

        return

    if not away_matches:

        await update.message.reply_text(
            f"❌ No recent {SEASON} data found "
            f"for {team_name(away_team)}."
        )

        return

    # --------------------------------------------------------
    # RECENT
    # --------------------------------------------------------

    home_recent = home_matches[:SAMPLE_SIZE]
    away_recent = away_matches[:SAMPLE_SIZE]

    home_stats = calculate_stats(
        home_recent
    )

    away_stats = calculate_stats(
        away_recent
    )

    # --------------------------------------------------------
    # VENUE
    # --------------------------------------------------------

    home_venue_matches = [
        m for m in home_matches
        if m["venue"] == "HOME"
    ][:SAMPLE_SIZE]

    away_venue_matches = [
        m for m in away_matches
        if m["venue"] == "AWAY"
    ][:SAMPLE_SIZE]

    home_venue = calculate_stats(
        home_venue_matches
    )

    away_venue = calculate_stats(
        away_venue_matches
    )

    # --------------------------------------------------------
    # XG
    # --------------------------------------------------------

    home_xg, away_xg = calculate_xg(
        home_stats,
        away_stats,
        home_venue,
        away_venue
    )

    total_xg = home_xg + away_xg

    # --------------------------------------------------------
    # MODELS
    # --------------------------------------------------------

    poisson = poisson_markets(
        home_xg,
        away_xg
    )

    historical = historical_markets(
        home_stats,
        away_stats,
        home_venue,
        away_venue
    )

    strength = sample_strength(
        home_stats["games"],
        away_stats["games"],
        home_venue["games"],
        away_venue["games"]
    )

    markets = build_markets(
        poisson,
        historical,
        strength
    )

    # --------------------------------------------------------
    # SIGNALS
    # --------------------------------------------------------

    signal_keys = [
        "1x",
        "x2",
        "over05",
        "over15",
        "over25",
        "under35",
        "under45",
        "btts",
        "home_score",
        "away_score",
    ]

    signals = [
        (key, markets[key])
        for key in signal_keys
    ]

    signals.sort(
        key=lambda item: item[1],
        reverse=True
    )

    top_signal = signals[0]
    second_signal = signals[1]

    signal_gap = (
        top_signal[1]
        - second_signal[1]
    )

    if (
        top_signal[1] >= 60
        and signal_gap >= 3
    ):

        primary_signal = top_signal

    else:

        primary_signal = None

    # --------------------------------------------------------
    # SCORELINES
    # --------------------------------------------------------

    scorelines = top_scorelines(
        home_xg,
        away_xg,
        count=5
    )

    # --------------------------------------------------------
    # MODEL QUALITY
    # --------------------------------------------------------

    disagreement = disagreement_level(
        poisson[top_signal[0]],
        historical[top_signal[0]]
    )

    model_conf = model_confidence(
        top_signal[1],
        strength,
        disagreement
    )

    result_conflict = (
        markets["1x"] >= 62
        and markets["x2"] >= 62
        and abs(
            markets["1x"]
            - markets["x2"]
        ) <= 8
    )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    lines = [
        f"⚽ GOALLOGIC AI v{VERSION}",
        "",
        f"🏟️ {team_name(home_team)} vs "
        f"{team_name(away_team)}",
        "",
        f"Season: {SEASON}",
        f"Sample: Last "
        f"{min(len(home_recent), len(away_recent))} "
        "available matches",
        "",
    ]

    # HOME FORM

    lines.append(
        f"🏠 {team_name(home_team)} recent:"
    )

    lines.append(
        f"{result_string(home_recent)} | "
        f"W/D/L "
        f"{home_stats['wins']}/"
        f"{home_stats['draws']}/"
        f"{home_stats['losses']} | "
        f"GF{home_stats['gf']} "
        f"GA{home_stats['ga']}"
    )

    lines.append(
        f"Avg "
        f"{home_stats['avg_gf']:.2f}/"
        f"{home_stats['avg_ga']:.2f} | "
        f"O1.5 "
        f"{home_stats['over15']:.0f}% | "
        f"O2.5 "
        f"{home_stats['over25']:.0f}% | "
        f"U3.5 "
        f"{home_stats['under35']:.0f}% | "
        f"BTTS "
        f"{home_stats['btts']:.0f}%"
    )

    lines.append(
        f"Scoring "
        f"{home_stats['scoring']:.0f}% | "
        f"CS "
        f"{home_stats['clean_sheet']:.0f}%"
    )

    lines.append("")

    # AWAY FORM

    lines.append(
        f"✈️ {team_name(away_team)} recent:"
    )

    lines.append(
        f"{result_string(away_recent)} | "
        f"W/D/L "
        f"{away_stats['wins']}/"
        f"{away_stats['draws']}/"
        f"{away_stats['losses']} | "
        f"GF{away_stats['gf']} "
        f"GA{away_stats['ga']}"
    )

    lines.append(
        f"Avg "
        f"{away_stats['avg_gf']:.2f}/"
        f"{away_stats['avg_ga']:.2f} | "
        f"O1.5 "
        f"{away_stats['over15']:.0f}% | "
        f"O2.5 "
        f"{away_stats['over25']:.0f}% | "
        f"U3.5 "
        f"{away_stats['under35']:.0f}% | "
        f"BTTS "
        f"{away_stats['btts']:.0f}%"
    )

    lines.append(
        f"Scoring "
        f"{away_stats['scoring']:.0f}% | "
        f"CS "
        f"{away_stats['clean_sheet']:.0f}%"
    )

    lines.append("")

    # VENUE

    lines.append(
        f"🏟️ {team_name(home_team)} HOME:"
    )

    if home_venue["games"] > 0:

        lines.append(
            f"{home_venue['games']} games | "
            f"W/D/L "
            f"{home_venue['wins']}/"
            f"{home_venue['draws']}/"
            f"{home_venue['losses']} | "
            f"Avg "
            f"{home_venue['avg_gf']:.2f}/"
            f"{home_venue['avg_ga']:.2f}"
        )

        lines.append(
            f"Scoring "
            f"{home_venue['scoring']:.0f}% | "
            f"CS "
            f"{home_venue['clean_sheet']:.0f}%"
        )

    else:

        lines.append(
            "No home matches in current sample."
        )

    lines.append("")

    lines.append(
        f"✈️ {team_name(away_team)} AWAY:"
    )

    if away_venue["games"] > 0:

        lines.append(
            f"{away_venue['games']} games | "
            f"W/D/L "
            f"{away_venue['wins']}/"
            f"{away_venue['draws']}/"
            f"{away_venue['losses']} | "
            f"Avg "
            f"{away_venue['avg_gf']:.2f}/"
            f"{away_venue['avg_ga']:.2f}"
        )

        lines.append(
            f"Scoring "
            f"{away_venue['scoring']:.0f}% | "
            f"CS "
            f"{away_venue['clean_sheet']:.0f}%"
        )

    else:

        lines.append(
            "No away matches in current sample."
        )

    lines.append("")

    # XG

    lines.append("📊 EXPECTED GOALS MODEL")

    lines.append(
        f"{team_name(home_team)}: "
        f"{home_xg:.2f} xG"
    )

    lines.append(
        f"{team_name(away_team)}: "
        f"{away_xg:.2f} xG"
    )

    lines.append(
        f"Total: {total_xg:.2f} xG"
    )

    lines.append("")

    # RESULT MODEL

    lines.append("🎯 RESULT MODEL")

    lines.append(
        f"1: {markets['home_win']:.0f}%"
        f" | X: {markets['draw']:.0f}%"
        f" | 2: {markets['away_win']:.0f}%"
    )

    lines.append("")

    # MARKET MODEL

    lines.append("📈 MARKET MODEL")

    display_keys = [
        "1x",
        "x2",
        "over05",
        "over15",
        "over25",
        "under35",
        "under45",
        "btts",
        "home_score",
        "away_score",
    ]

    for key in display_keys:

        lines.append(
            f"{market_label(key)}: "
            f"{markets[key]:.0f}%"
        )

    lines.append("")

    # SCORELINES

    lines.append("🥅 TOP SCORELINES")

    for (home_goals, away_goals), probability in scorelines[:3]:

        lines.append(
            f"{home_goals}-{away_goals}: "
            f"{probability * 100:.0f}%"
        )

    lines.append("")

    # TOP SIGNALS

    lines.append("🔥 TOP MODEL SIGNALS")

    for key, value in signals[:3]:

        lines.append(
            f"{confidence_icon(value)} "
            f"{market_label(key)} "
            f"{value:.0f}%"
        )

    lines.append("")

    # MODEL QUALITY

    lines.append("🧠 MODEL QUALITY")

    lines.append(
        f"Data strength: "
        f"{strength * 100:.0f}%"
    )

    lines.append(
        f"Top-signal disagreement: "
        f"{disagreement}"
    )

    lines.append(
        f"Model confidence: "
        f"{confidence_icon(model_conf)} "
        f"{model_conf:.0f}% "
        f"({confidence_label(model_conf)})"
    )

    if result_conflict:

        lines.append(
            "⚠️ 1X and X2 are both elevated; "
            "the result market is not clearly separated."
        )

    if primary_signal:

        lines.append("")

        lines.append(
            f"📌 PRIMARY MODEL SIGNAL: "
            f"{market_label(primary_signal[0])} "
            f"{primary_signal[1]:.0f}%"
        )

    else:

        lines.append("")

        lines.append(
            "📌 PRIMARY MODEL SIGNAL: "
            "No clear separation."
        )

    lines.append("")

    lines.append(
        "ℹ️ Probabilities are model estimates "
        "from recent 2026/27 data, venue evidence "
        "and a Poisson score model. They are not guarantees."
    )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    start_health_server()

    print(
        f"GoalLogic AI v{VERSION} "
        "Telegram bot is starting..."
    )

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "apitest",
            apitest_command
        )
    )

    application.add_handler(
        CommandHandler(
            "team",
            team_command
        )
    )

    application.add_handler(
        CommandHandler(
            "fixtures",
            fixtures_command
        )
    )

    application.add_handler(
        CommandHandler(
            "analyze",
            analyze_command
        )
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
