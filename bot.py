import os
import math
import threading
import http.server
import statistics
from datetime import datetime

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# GOALLOGIC AI v2.8
# ============================================================

VERSION = "2.8"

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

    print(f"GoalLogic AI v{VERSION} is running on port {PORT}.")

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
                message = error.get(
                    "message",
                    f"HTTP {response.status_code}"
                )
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

def safe_float(value):
    try:
        if value is None:
            return None

        if isinstance(value, bool):
            return None

        return float(value)

    except Exception:
        return None


def safe_int(value):
    try:
        if value is None:
            return None

        if isinstance(value, bool):
            return None

        return int(float(value))

    except Exception:
        return None


def clean_name(name):
    return " ".join(str(name or "").strip().lower().split())


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
        data = data.get("teams", data.get("results", []))

    if not isinstance(data, list):
        return [], "Unexpected search response."

    return data, None


def find_team(query):
    teams, error = search_team(query)

    if error:
        return None, error

    if not teams:
        return None, f"No team found for {query}."

    wanted = clean_name(query)

    # Exact name first
    for team in teams:
        name = clean_name(team_name(team))

        if name == wanted:
            return team, None

    # Contains match
    for team in teams:
        name = clean_name(team_name(team))

        if wanted in name or name in wanted:
            return team, None

    return teams[0], None


# ============================================================
# SCORE EXTRACTION
#
# OpenFoot can expose normalized match information in different
# structures depending on endpoint/source. v2.8 checks several
# safe structures instead of assuming only one.
# ============================================================

def extract_score_value(value):
    """
    Convert a score value into an integer when possible.
    """

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None

    if isinstance(value, str):
        value = value.strip()

        if value.isdigit():
            return int(value)

    return None


def extract_scores(match):
    """
    Returns:
        (home_score, away_score)

    Tries multiple common OpenFoot score structures.
    """

    if not isinstance(match, dict):
        return None, None

    # --------------------------------------------------------
    # 1. Direct fields
    # --------------------------------------------------------

    direct_pairs = [
        ("homeScore", "awayScore"),
        ("homeGoals", "awayGoals"),
        ("home_score", "away_score"),
        ("home_goals", "away_goals"),
    ]

    for home_key, away_key in direct_pairs:
        h = extract_score_value(match.get(home_key))
        a = extract_score_value(match.get(away_key))

        if h is not None and a is not None:
            return h, a

    # --------------------------------------------------------
    # 2. Nested "score" object
    # --------------------------------------------------------

    score = match.get("score")

    if isinstance(score, dict):

        # Common:
        # score.home / score.away
        for home_key, away_key in [
            ("home", "away"),
            ("homeScore", "awayScore"),
            ("homeGoals", "awayGoals"),
            ("home_score", "away_score"),
            ("home_goals", "away_goals"),
        ]:
            h = extract_score_value(score.get(home_key))
            a = extract_score_value(score.get(away_key))

            if h is not None and a is not None:
                return h, a

        # Nested score.home.score / score.away.score
        home_obj = score.get("home")
        away_obj = score.get("away")

        if isinstance(home_obj, dict) and isinstance(away_obj, dict):

            for hk, ak in [
                ("score", "score"),
                ("goals", "goals"),
                ("current", "current"),
                ("fullTime", "fullTime"),
            ]:
                h = extract_score_value(home_obj.get(hk))
                a = extract_score_value(away_obj.get(ak))

                if h is not None and a is not None:
                    return h, a

    # --------------------------------------------------------
    # 3. "scores" object
    # --------------------------------------------------------

    scores = match.get("scores")

    if isinstance(scores, dict):

        for home_key, away_key in [
            ("home", "away"),
            ("homeScore", "awayScore"),
            ("homeGoals", "awayGoals"),
            ("home_score", "away_score"),
            ("away_score", "home_score"),
        ]:
            h = extract_score_value(scores.get(home_key))
            a = extract_score_value(scores.get(away_key))

            if h is not None and a is not None:
                return h, a

    # --------------------------------------------------------
    # 4. Team objects may contain scores
    # --------------------------------------------------------

    home_team = match.get("homeTeam", {})
    away_team = match.get("awayTeam", {})

    if isinstance(home_team, dict) and isinstance(away_team, dict):

        for home_key, away_key in [
            ("score", "score"),
            ("goals", "goals"),
            ("homeScore", "awayScore"),
            ("homeGoals", "awayGoals"),
        ]:
            h = extract_score_value(home_team.get(home_key))
            a = extract_score_value(away_team.get(away_key))

            if h is not None and a is not None:
                return h, a

    # --------------------------------------------------------
    # 5. Result object
    # --------------------------------------------------------

    result = match.get("result")

    if isinstance(result, dict):

        for home_key, away_key in [
            ("home", "away"),
            ("homeScore", "awayScore"),
            ("homeGoals", "awayGoals"),
        ]:
            h = extract_score_value(result.get(home_key))
            a = extract_score_value(result.get(away_key))

            if h is not None and a is not None:
                return h, a

    return None, None


# ============================================================
# MATCH STATUS
# ============================================================

def get_status(match):
    if not isinstance(match, dict):
        return ""

    return str(
        match.get("status")
        or match.get("matchStatus")
        or ""
    ).strip().lower()


def is_finished(match):
    status = get_status(match)

    return status in {
        "finished",
        "ft",
        "fulltime",
        "full-time",
        "completed",
        "complete",
    }


# ============================================================
# MATCH DATE
# ============================================================

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
        text = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


# ============================================================
# GET TEAM MATCHES
# ============================================================

def get_team_matches(team_id_value):
    """
    Retrieve all matches for a team and season.

    We intentionally do NOT send status=finished to the API.
    Instead we retrieve the season matches and filter completed
    matches locally. This makes the bot more tolerant of API
    source/status differences.
    """

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

        home_team = match.get("homeTeam", {})
        away_team = match.get("awayTeam", {})

        home_id = team_id(home_team)
        away_id = team_id(away_team)

        home_score, away_score = extract_scores(match)

        # We require a valid score because the bot's historical
        # calculations need an actual completed result.
        if home_score is None or away_score is None:
            continue

        # If status is explicitly scheduled/live/postponed/etc.,
        # don't treat it as historical.
        status = get_status(match)

        if status in {
            "scheduled",
            "live",
            "postponed",
            "cancelled",
            "canceled",
        }:
            continue

        # A valid score is strong evidence that this is completed.
        # This also protects us if the provider omits "finished".
        completed.append({
            "id": match.get("id"),
            "status": status or "finished",
            "kickoffAt": match.get("kickoffAt"),
            "homeTeam": home_team,
            "awayTeam": away_team,
            "_home_score": home_score,
            "_away_score": away_score,
            "_home_id": home_id,
            "_away_id": away_id,
        })

    completed.sort(
        key=lambda x: match_datetime(x) or datetime.min,
        reverse=True
    )

    return completed, None


# ============================================================
# TEAM-ORIENTED MATCHES
# ============================================================

def team_oriented_matches(team, matches):
    target_id = team_id(team)
    target_name = clean_name(team_name(team))

    result = []

    for match in matches:

        home = match.get("homeTeam", {})
        away = match.get("awayTeam", {})

        home_id = team_id(home)
        away_id = team_id(away)

        is_home = False

        if target_id and home_id == target_id:
            is_home = True

        elif target_id and away_id == target_id:
            is_home = False

        else:
            home_name = clean_name(team_name(home))
            away_name = clean_name(team_name(away))

            if target_name == home_name:
                is_home = True
            elif target_name == away_name:
                is_home = False
            else:
                continue

        hs = match.get("_home_score")
        aws = match.get("_away_score")

        if hs is None or aws is None:
            continue

        if is_home:
            gf = hs
            ga = aws
            venue = "HOME"
            opponent = team_name(away)
        else:
            gf = aws
            ga = hs
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
        key=lambda x: (
            datetime.fromisoformat(
                str(x["date"]).replace("Z", "+00:00")
            )
            if x.get("date")
            else datetime.min
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

    wins = sum(1 for m in matches if m["result"] == "W")
    draws = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")

    gf = sum(m["gf"] for m in matches)
    ga = sum(m["ga"] for m in matches)

    over15 = sum(
        1 for m in matches
        if m["gf"] + m["ga"] > 1
    )

    over25 = sum(
        1 for m in matches
        if m["gf"] + m["ga"] > 2
    )

    under35 = sum(
        1 for m in matches
        if m["gf"] + m["ga"] < 4
    )

    btts = sum(
        1 for m in matches
        if m["gf"] > 0 and m["ga"] > 0
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


def poisson_matrix(home_xg, away_xg, max_goals=8):
    matrix = {}

    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):

            probability = (
                poisson_probability(home_xg, home_goals)
                * poisson_probability(away_xg, away_goals)
            )

            matrix[(home_goals, away_goals)] = probability

    total = sum(matrix.values())

    if total > 0:
        matrix = {
            key: value / total
            for key, value in matrix.items()
        }

    return matrix


def poisson_markets(home_xg, away_xg):
    matrix = poisson_matrix(home_xg, away_xg)

    home_win = 0
    draw = 0
    away_win = 0

    over15 = 0
    over25 = 0
    under35 = 0
    btts = 0

    home_score = 0
    away_score = 0

    for (hg, ag), probability in matrix.items():

        if hg > ag:
            home_win += probability
        elif hg == ag:
            draw += probability
        else:
            away_win += probability

        if hg + ag >= 2:
            over15 += probability

        if hg + ag >= 3:
            over25 += probability

        if hg + ag <= 3:
            under35 += probability

        if hg > 0 and ag > 0:
            btts += probability

        if hg > 0:
            home_score += probability

        if ag > 0:
            away_score += probability

    return {
        "home_win": home_win * 100,
        "draw": draw * 100,
        "away_win": away_win * 100,

        "1x": (home_win + draw) * 100,
        "x2": (draw + away_win) * 100,

        "over15": over15 * 100,
        "over25": over25 * 100,
        "under35": under35 * 100,
        "btts": btts * 100,

        "home_score": home_score * 100,
        "away_score": away_score * 100,
    }


# ============================================================
# xG ESTIMATION
# ============================================================

def calculate_xg(
    home_recent,
    away_recent,
    home_venue,
    away_venue
):
    """
    Conservative historical xG estimate.

    Uses:
    - recent attacking output
    - recent defensive concession
    - venue scoring/conceding
    """

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

    # Recent evidence gets more weight than venue evidence
    # because venue samples can be small.
    home_base = (
        home_attack * 0.35
        + away_defense * 0.30
        + home_venue_attack * 0.20
        + away_venue_defense * 0.15
    )

    away_base = (
        away_attack * 0.35
        + home_defense * 0.30
        + away_venue_attack * 0.20
        + home_venue_defense * 0.15
    )

    # Keep estimates in a realistic range.
    home_xg = max(0.20, min(3.50, home_base))
    away_xg = max(0.20, min(3.50, away_base))

    return home_xg, away_xg


# ============================================================
# HISTORICAL MARKET ESTIMATES
# ============================================================

def historical_markets(
    home_stats,
    away_stats,
    home_venue,
    away_venue
):
    """
    Historical secondary model.

    It is deliberately weaker than Poisson so the mathematical
    score model remains the primary source.
    """

    home_unbeaten = (
        home_stats["wins"] + home_stats["draws"]
    ) / max(1, home_stats["games"]) * 100

    away_unbeaten = (
        away_stats["wins"] + away_stats["draws"]
    ) / max(1, away_stats["games"]) * 100

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

    return {
        "1x": (
            home_unbeaten * 0.60
            + home_venue["wins"]
            / max(1, home_venue["games"])
            * 100
            * 0.40
        ),

        "x2": (
            away_unbeaten * 0.60
            + (
                away_venue["wins"]
                + away_venue["draws"]
            )
            / max(1, away_venue["games"])
            * 100
            * 0.40
        ),

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

        "btts": (
            home_stats["btts"]
            + away_stats["btts"]
        ) / 2,

        "home_score": (
            home_stats["scoring"]
            + home_venue["scoring"]
        ) / 2,

        "away_score": (
            away_stats["scoring"]
            + away_venue["scoring"]
        ) / 2,

        "home_win": home_win_rate,
        "away_win": away_win_rate,
    }


# ============================================================
# SAMPLE-SIZE CONTROL
# ============================================================

def sample_weight(games):
    if games <= 1:
        return 0.20

    if games == 2:
        return 0.30

    if games == 3:
        return 0.40

    if games == 4:
        return 0.50

    return 0.60


def shrink_probability(value, games):
    """
    Pulls extreme historical estimates toward 50 when samples
    are small.
    """

    weight = sample_weight(games)

    return 50 + (value - 50) * weight


# ============================================================
# BLENDING
# ============================================================

def blend_market(
    poisson_value,
    historical_value,
    games
):
    """
    Poisson is the primary model.

    Historical evidence provides a controlled secondary adjustment.
    """

    raw = (
        poisson_value * 0.75
        + historical_value * 0.25
    )

    return max(
        1.0,
        min(
            99.0,
            shrink_probability(raw, games)
        )
    )


def build_markets(
    poisson,
    historical,
    home_games,
    away_games
):
    games = min(home_games, away_games)

    markets = {}

    for key in [
        "1x",
        "x2",
        "over15",
        "over25",
        "under35",
        "btts",
        "home_score",
        "away_score",
    ]:
        markets[key] = blend_market(
            poisson[key],
            historical[key],
            games
        )

    # Result probabilities are derived from Poisson directly.
    markets["home_win"] = blend_market(
        poisson["home_win"],
        historical["home_win"],
        games
    )

    markets["draw"] = poisson["draw"]
    markets["away_win"] = blend_market(
        poisson["away_win"],
        historical["away_win"],
        games
    )

    return markets


# ============================================================
# DISPLAY
# ============================================================

def market_label(key):
    labels = {
        "1x": "1X",
        "x2": "X2",
        "over15": "Over 1.5",
        "over25": "Over 2.5",
        "under35": "Under 3.5",
        "btts": "BTTS",
        "home_score": "Team 1 to score",
        "away_score": "Team 2 to score",
        "home_win": "Team 1 win",
        "draw": "Draw",
        "away_win": "Team 2 win",
    }

    return labels.get(key, key)


def confidence_icon(value):
    if value >= 75:
        return "🟢"

    if value >= 60:
        return "🟡"

    return "🔴"


def result_string(matches):
    return "".join(
        m["result"]
        for m in matches
    )


def format_stats(name, stats, matches):
    form = result_string(matches)

    return (
        f"⚽ {name}\n"
        f"Recent: {form}\n"
        f"W/D/L: {stats['wins']}/{stats['draws']}/{stats['losses']}\n"
        f"GF {stats['gf']} GA {stats['ga']}\n"
        f"Avg goals: {stats['avg_gf']:.2f}\n"
        f"Avg conceded: {stats['avg_ga']:.2f}\n"
        f"Over 1.5: {stats['over15']:.0f}%\n"
        f"Over 2.5: {stats['over25']:.0f}%\n"
        f"Under 3.5: {stats['under35']:.0f}%\n"
        f"BTTS: {stats['btts']:.0f}%\n"
        f"Scoring: {stats['scoring']:.0f}%\n"
        f"Clean sheet: {stats['clean_sheet']:.0f}%"
    )


# ============================================================
# COMMAND: /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = (
        f"⚽ Welcome to GoalLogic AI v{VERSION}!\n\n"
        "I analyze football matches using historical "
        "statistics, venue evidence, expected goals and "
        "a Poisson probability model.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )

    await update.message.reply_text(text)


# ============================================================
# COMMAND: /APITEST
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
            "❌ OpenFoot API connected, but Chelsea "
            "was not found."
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
# COMMAND: /TEAM
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

    query = " ".join(context.args)

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
        f"🔎 Teams found for: {query}\n"
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
# COMMAND: /FIXTURES
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

    query = " ".join(context.args)

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
            f"❌ Could not retrieve matches:\n{error}"
        )
        return

    oriented = team_oriented_matches(
        team,
        matches
    )

    if not oriented:
        await update.message.reply_text(
            f"❌ No completed {SEASON} matches found "
            f"for {team_name(team)}."
        )
        return

    sample = oriented[:SAMPLE_SIZE]

    lines = [
        f"📅 {team_name(team)}",
        f"Season: {SEASON}",
        f"Showing last {len(sample)} completed matches\n",
    ]

    for match in sample:

        date_text = str(
            match.get("date") or ""
        )[:10]

        opponent = match["opponent"]

        lines.append(
            f"{date_text} | "
            f"{match['venue']} | "
            f"{match['result']} | "
            f"{match['gf']}-{match['ga']} "
            f"vs {opponent}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# COMMAND: /ANALYZE
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

    raw = " ".join(context.args)

    if " vs " not in raw.lower():
        await update.message.reply_text(
            "❌ Use this format:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    parts = raw.lower().split(" vs ", 1)

    home_query = parts[0].strip()
    away_query = parts[1].strip()

    if not home_query or not away_query:
        await update.message.reply_text(
            "❌ Please provide both teams."
        )
        return

    # --------------------------------------------------------
    # Find teams
    # --------------------------------------------------------

    home_team, error = find_team(home_query)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    away_team, error = find_team(away_query)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    # --------------------------------------------------------
    # Get matches
    # --------------------------------------------------------

    home_matches_raw, error = get_team_matches(
        team_id(home_team)
    )

    if error:
        await update.message.reply_text(
            f"❌ Could not retrieve data for "
            f"{team_name(home_team)}:\n{error}"
        )
        return

    away_matches_raw, error = get_team_matches(
        team_id(away_team)
    )

    if error:
        await update.message.reply_text(
            f"❌ Could not retrieve data for "
            f"{team_name(away_team)}:\n{error}"
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
    # Samples
    # --------------------------------------------------------

    home_recent = home_matches[:SAMPLE_SIZE]
    away_recent = away_matches[:SAMPLE_SIZE]

    home_stats = calculate_stats(home_recent)
    away_stats = calculate_stats(away_recent)

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
    # xG
    # --------------------------------------------------------

    home_xg, away_xg = calculate_xg(
        home_stats,
        away_stats,
        home_venue,
        away_venue
    )

    total_xg = home_xg + away_xg

    # --------------------------------------------------------
    # Poisson
    # --------------------------------------------------------

    poisson = poisson_markets(
        home_xg,
        away_xg
    )

    # --------------------------------------------------------
    # Historical
    # --------------------------------------------------------

    historical = historical_markets(
        home_stats,
        away_stats,
        home_venue,
        away_venue
    )

    # --------------------------------------------------------
    # Final markets
    # --------------------------------------------------------

    markets = build_markets(
        poisson,
        historical,
        home_stats["games"],
        away_stats["games"]
    )

    # --------------------------------------------------------
    # Signals
    # --------------------------------------------------------

    signal_keys = [
        "1x",
        "x2",
        "over15",
        "over25",
        "under35",
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

    # --------------------------------------------------------
    # Result conflict
    # --------------------------------------------------------

    result_conflict = (
        markets["1x"] >= 65
        and markets["x2"] >= 65
        and abs(
            markets["1x"] - markets["x2"]
        ) <= 8
    )

    # --------------------------------------------------------
    # Build output
    # --------------------------------------------------------

    lines = [
        f"⚽ GOALLOGIC AI v{VERSION}",
        "",
        f"🏟️ {team_name(home_team)} vs "
        f"{team_name(away_team)}",
        "",
        f"Season: {SEASON}",
        f"Sample: Last {min(len(home_recent), len(away_recent))} available matches",
        "",
    ]

    lines.append(
        f"🏠 {team_name(home_team)} recent:"
    )

    lines.append(
        f"{result_string(home_recent)} | "
        f"W/D/L {home_stats['wins']}/"
        f"{home_stats['draws']}/"
        f"{home_stats['losses']} | "
        f"GF{home_stats['gf']} GA{home_stats['ga']}"
    )

    lines.append(
        f"Avg {home_stats['avg_gf']:.2f}/"
        f"{home_stats['avg_ga']:.2f} | "
        f"O1.5 {home_stats['over15']:.0f}% | "
        f"O2.5 {home_stats['over25']:.0f}% | "
        f"U3.5 {home_stats['under35']:.0f}% | "
        f"BTTS {home_stats['btts']:.0f}%"
    )

    lines.append(
        f"Scoring {home_stats['scoring']:.0f}% | "
        f"CS {home_stats['clean_sheet']:.0f}%"
    )

    lines.append("")

    lines.append(
        f"✈️ {team_name(away_team)} recent:"
    )

    lines.append(
        f"{result_string(away_recent)} | "
        f"W/D/L {away_stats['wins']}/"
        f"{away_stats['draws']}/"
        f"{away_stats['losses']} | "
        f"GF{away_stats['gf']} GA{away_stats['ga']}"
    )

    lines.append(
        f"Avg {away_stats['avg_gf']:.2f}/"
        f"{away_stats['avg_ga']:.2f} | "
        f"O1.5 {away_stats['over15']:.0f}% | "
        f"O2.5 {away_stats['over25']:.0f}% | "
        f"U3.5 {away_stats['under35']:.0f}% | "
        f"BTTS {away_stats['btts']:.0f}%"
    )

    lines.append(
        f"Scoring {away_stats['scoring']:.0f}% | "
        f"CS {away_stats['clean_sheet']:.0f}%"
    )

    lines.append("")

    lines.append(
        f"🏠 {team_name(home_team)} HOME: "
        f"{home_venue['games']} games | "
        f"W/D/L {home_venue['wins']}/"
        f"{home_venue['draws']}/"
        f"{home_venue['losses']} | "
        f"Scoring {home_venue['scoring']:.0f}% | "
        f"CS {home_venue['clean_sheet']:.0f}%"
    )

    lines.append(
        f"✈️ {team_name(away_team)} AWAY: "
        f"{away_venue['games']} games | "
        f"W/D/L {away_venue['wins']}/"
        f"{away_venue['draws']}/"
        f"{away_venue['losses']} | "
        f"Scoring {away_venue['scoring']:.0f}% | "
        f"CS {away_venue['clean_sheet']:.0f}%"
    )

    lines.append("")

    lines.append(
        "📊 POISSON MODEL"
    )

    lines.append(
        f"xG: {home_xg:.2f} - {away_xg:.2f}"
    )

    lines.append(
        f"Total xG: {total_xg:.2f}"
    )

    lines.append("")

    lines.append(
        "📈 PROBABILITIES"
    )

    display_order = [
        "1x",
        "x2",
        "over15",
        "over25",
        "under35",
        "btts",
        "home_score",
        "away_score",
    ]

    for key in display_order:
        value = markets[key]

        lines.append(
            f"{confidence_icon(value)} "
            f"{market_label(key)}: "
            f"{value:.0f}%"
        )

    lines.append("")

    lines.append(
        "🔥 TOP SIGNALS"
    )

    for key, value in signals[:3]:
        lines.append(
            f"{confidence_icon(value)} "
            f"{market_label(key)} "
            f"{value:.0f}%"
        )

    if result_conflict:
        lines.append("")
        lines.append(
            "⚠️ Result-market conflict: "
            "1X and X2 are both elevated. "
            "This indicates uncertainty in the match result."
        )

    lines.append("")

    if top_signal[1] >= 60:
        lines.append(
            f"🎯 PRIMARY SIGNAL: "
            f"{market_label(top_signal[0])} "
            f"{top_signal[1]:.0f}%"
        )
    else:
        lines.append(
            "🎯 PRIMARY SIGNAL: "
            "No market reaches the 60% threshold."
        )

    lines.append("")
    lines.append(
        "ℹ️ Model note: probabilities combine "
        "Poisson score modelling with recent and "
        "venue-based historical evidence."
    )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    print(
        "Telegram error:",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not OPENFOOT_API_KEY:
        raise RuntimeError(
            "OPENFOOT_API_KEY is missing."
        )

    start_health_server()

    print(
        f"GoalLogic AI v{VERSION} Telegram bot is starting..."
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

    application.add_error_handler(
        error_handler
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
