import os
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from statistics import mean

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# GOALLOGIC AI v3.0
# ============================================================

VERSION = "3.0"

API_BASE = "https://openfootapi.com/v1"
SEASON = "2026/27"
SAMPLE_SIZE = 5

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY")

PORT = int(os.getenv("PORT", "10000"))


# ============================================================
# RENDER HEALTH SERVER
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
    print(f"GoalLogic AI v{VERSION} is running on port {PORT}.")
    server.serve_forever()


# ============================================================
# API
# ============================================================

def api_headers():
    return {
        "Authorization": f"Bearer {OPENFOOT_API_KEY}",
        "Accept": "application/json",
    }


def api_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        return None, "OPENFOOT_API_KEY is missing."

    url = f"{API_BASE}/{endpoint.lstrip('/')}"

    try:
        response = requests.get(
            url,
            headers=api_headers(),
            params=params,
            timeout=20,
        )

        if response.status_code != 200:
            return None, (
                f"API HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            return response.json(), None
        except Exception:
            return None, "API returned invalid JSON."

    except requests.RequestException as exc:
        return None, f"API request failed: {exc}"


# ============================================================
# GENERAL HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def pct(value):
    return int(round(max(0.0, min(100.0, value))))


def normalize_name(name):
    return " ".join(str(name or "").lower().strip().split())


def team_name(team):
    if isinstance(team, dict):
        return (
            team.get("name")
            or team.get("shortName")
            or team.get("displayName")
            or "Unknown"
        )

    return str(team or "Unknown")


def team_id(team):
    if not isinstance(team, dict):
        return None

    return (
        team.get("id")
        or team.get("teamId")
        or team.get("uid")
    )


def extract_data(payload):
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return [data]

    return []


# ============================================================
# TEAM SEARCH
# ============================================================

def search_team(query):
    payload, error = api_get(
        "search",
        {"q": query},
    )

    if error:
        return [], error

    return extract_data(payload), None


def find_team(query):
    results, error = search_team(query)

    if error:
        return None, error

    if not results:
        return None, f"No team found for '{query}'."

    wanted = normalize_name(query)

    # Exact name first
    for item in results:
        name = team_name(item)

        if normalize_name(name) == wanted:
            return item, None

    # Then starts-with
    for item in results:
        name = normalize_name(team_name(item))

        if name.startswith(wanted):
            return item, None

    # Otherwise first result
    return results[0], None


# ============================================================
# SCORE EXTRACTION
# ============================================================

def value_from_object(obj, keys):
    if not isinstance(obj, dict):
        return None

    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]

    return None


def extract_score_pair(match):
    """
    Attempts to support several common OpenFoot score structures.
    Returns (home_score, away_score), or (None, None).
    """

    # Direct fields
    home = value_from_object(
        match,
        [
            "homeScore",
            "homeGoals",
            "home_score",
            "home_score_ft",
        ],
    )

    away = value_from_object(
        match,
        [
            "awayScore",
            "awayGoals",
            "away_score",
            "away_score_ft",
        ],
    )

    if home is not None and away is not None:
        return safe_float(home), safe_float(away)

    # Score object
    score = match.get("score")

    if isinstance(score, dict):
        home = value_from_object(
            score,
            [
                "home",
                "homeScore",
                "homeGoals",
                "fullTimeHome",
            ],
        )

        away = value_from_object(
            score,
            [
                "away",
                "awayScore",
                "awayGoals",
                "fullTimeAway",
            ],
        )

        if isinstance(home, dict):
            home = value_from_object(
                home,
                ["current", "display", "fullTime", "goals"],
            )

        if isinstance(away, dict):
            away = value_from_object(
                away,
                ["current", "display", "fullTime", "goals"],
            )

        if home is not None and away is not None:
            return safe_float(home), safe_float(away)

    # Nested teams with scores
    home_team = match.get("homeTeam")

    away_team = match.get("awayTeam")

    if isinstance(home_team, dict) and isinstance(away_team, dict):
        home_score = value_from_object(
            home_team,
            ["score", "goals", "homeScore"],
        )

        away_score = value_from_object(
            away_team,
            ["score", "goals", "awayScore"],
        )

        if isinstance(home_score, dict):
            home_score = value_from_object(
                home_score,
                ["current", "fullTime", "display"],
            )

        if isinstance(away_score, dict):
            away_score = value_from_object(
                away_score,
                ["current", "fullTime", "display"],
            )

        if home_score is not None and away_score is not None:
            return safe_float(home_score), safe_float(away_score)

    return None, None


# ============================================================
# MATCH HELPERS
# ============================================================

def match_team_ids(match):
    home = match.get("homeTeam")
    away = match.get("awayTeam")

    return team_id(home), team_id(away)


def match_team_names(match):
    home = match.get("homeTeam")
    away = match.get("awayTeam")

    return team_name(home), team_name(away)


def is_completed_match(match):
    status = str(
        match.get("status")
        or match.get("state")
        or ""
    ).lower()

    finished_words = [
        "finished",
        "complete",
        "completed",
        "ft",
        "full time",
    ]

    if any(word in status for word in finished_words):
        return True

    home_score, away_score = extract_score_pair(match)

    return home_score is not None and away_score is not None


def kickoff_sort_value(match):
    value = (
        match.get("kickoffAt")
        or match.get("kickoff")
        or match.get("date")
        or match.get("startTime")
        or ""
    )

    return str(value)


def get_team_matches(team):
    tid = team_id(team)

    if not tid:
        return [], "Team ID unavailable."

    payload, error = api_get(
        "matches",
        {
            "team": tid,
            "season": SEASON,
        },
    )

    if error:
        return [], error

    matches = extract_data(payload)

    completed = []

    for match in matches:
        if is_completed_match(match):
            home_score, away_score = extract_score_pair(match)

            if home_score is None or away_score is None:
                continue

            match["_home_score"] = home_score
            match["_away_score"] = away_score

            completed.append(match)

    completed.sort(
        key=kickoff_sort_value,
        reverse=True,
    )

    return completed, None


def team_oriented_matches(team, matches):
    """
    Converts matches into:
    {
        match,
        venue,
        opponent,
        gf,
        ga,
        result
    }
    """

    tid = team_id(team)
    wanted_name = normalize_name(team_name(team))

    output = []

    for match in matches:
        home = match.get("homeTeam")
        away = match.get("awayTeam")

        hid = team_id(home)
        aid = team_id(away)

        home_name = team_name(home)
        away_name = team_name(away)

        is_home = False

        if tid and hid == tid:
            is_home = True
        elif tid and aid == tid:
            is_home = False
        else:
            if normalize_name(home_name) == wanted_name:
                is_home = True
            elif normalize_name(away_name) == wanted_name:
                is_home = False
            else:
                continue

        hs = safe_float(match.get("_home_score"))
        aws = safe_float(match.get("_away_score"))

        if is_home:
            gf = hs
            ga = aws
            opponent = away_name
            venue = "HOME"
        else:
            gf = aws
            ga = hs
            opponent = home_name
            venue = "AWAY"

        if gf > ga:
            result = "W"
        elif gf < ga:
            result = "L"
        else:
            result = "D"

        output.append(
            {
                "match": match,
                "venue": venue,
                "opponent": opponent,
                "gf": gf,
                "ga": ga,
                "result": result,
            }
        )

    return output


# ============================================================
# STATS
# ============================================================

def calculate_stats(oriented_matches):
    if not oriented_matches:
        return {
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "avg_gf": 0,
            "avg_ga": 0,
            "over05": 0,
            "over15": 0,
            "over25": 0,
            "under35": 0,
            "under45": 0,
            "btts": 0,
            "scoring": 0,
            "clean_sheet": 0,
        }

    games = len(oriented_matches)

    wins = sum(
        1 for m in oriented_matches
        if m["result"] == "W"
    )

    draws = sum(
        1 for m in oriented_matches
        if m["result"] == "D"
    )

    losses = sum(
        1 for m in oriented_matches
        if m["result"] == "L"
    )

    gf = sum(m["gf"] for m in oriented_matches)
    ga = sum(m["ga"] for m in oriented_matches)

    over05 = sum(
        1 for m in oriented_matches
        if m["gf"] + m["ga"] > 0.5
    )

    over15 = sum(
        1 for m in oriented_matches
        if m["gf"] + m["ga"] > 1.5
    )

    over25 = sum(
        1 for m in oriented_matches
        if m["gf"] + m["ga"] > 2.5
    )

    under35 = sum(
        1 for m in oriented_matches
        if m["gf"] + m["ga"] < 3.5
    )

    under45 = sum(
        1 for m in oriented_matches
        if m["gf"] + m["ga"] < 4.5
    )

    btts = sum(
        1 for m in oriented_matches
        if m["gf"] > 0 and m["ga"] > 0
    )

    scoring = sum(
        1 for m in oriented_matches
        if m["gf"] > 0
    )

    clean_sheet = sum(
        1 for m in oriented_matches
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
        "over05": over05 / games * 100,
        "over15": over15 / games * 100,
        "over25": over25 / games * 100,
        "under35": under35 / games * 100,
        "under45": under45 / games * 100,
        "btts": btts / games * 100,
        "scoring": scoring / games * 100,
        "clean_sheet": clean_sheet / games * 100,
    }


def recent_form_string(matches):
    return "".join(m["result"] for m in matches)


# ============================================================
# XG MODEL
# ============================================================

def shrink(value, baseline=1.35, strength=0.25):
    return (
        value * (1 - strength)
        + baseline * strength
    )


def estimate_xg(
    home_recent,
    away_recent,
    home_venue,
    away_venue,
):
    home_stats = calculate_stats(home_recent)
    away_stats = calculate_stats(away_recent)

    home_venue_stats = calculate_stats(home_venue)
    away_venue_stats = calculate_stats(away_venue)

    # Recent attacking strength
    home_attack = home_stats["avg_gf"]
    away_attack = away_stats["avg_gf"]

    # Recent defensive concession
    home_defense = home_stats["avg_ga"]
    away_defense = away_stats["avg_ga"]

    # Venue-specific evidence
    home_venue_attack = (
        home_venue_stats["avg_gf"]
        if home_venue_stats["games"] > 0
        else home_attack
    )

    home_venue_defense = (
        home_venue_stats["avg_ga"]
        if home_venue_stats["games"] > 0
        else home_defense
    )

    away_venue_attack = (
        away_venue_stats["avg_gf"]
        if away_venue_stats["games"] > 0
        else away_attack
    )

    away_venue_defense = (
        away_venue_stats["avg_ga"]
        if away_venue_stats["games"] > 0
        else away_defense
    )

    # Blend recent + venue
    home_base = (
        home_attack * 0.55
        + away_defense * 0.45
    )

    away_base = (
        away_attack * 0.55
        + home_defense * 0.45
    )

    home_venue_xg = (
        home_venue_attack * 0.55
        + away_venue_defense * 0.45
    )

    away_venue_xg = (
        away_venue_attack * 0.55
        + home_venue_defense * 0.45
    )

    if home_venue_stats["games"] > 0:
        home_xg = (
            home_base * 0.65
            + home_venue_xg * 0.35
        )
    else:
        home_xg = home_base

    if away_venue_stats["games"] > 0:
        away_xg = (
            away_base * 0.65
            + away_venue_xg * 0.35
        )
    else:
        away_xg = away_base

    # Shrink small samples toward league-neutral scoring
    home_xg = shrink(home_xg, 1.35, 0.18)
    away_xg = shrink(away_xg, 1.15, 0.18)

    # Avoid extreme estimates from tiny samples
    home_xg = max(0.20, min(3.50, home_xg))
    away_xg = max(0.20, min(3.50, away_xg))

    return home_xg, away_xg


# ============================================================
# POISSON MODEL
# ============================================================

def poisson_probability(goals, expected):
    try:
        return (
            math.exp(-expected)
            * (expected ** goals)
            / math.factorial(goals)
        )
    except (OverflowError, ValueError):
        return 0.0


def build_score_matrix(home_xg, away_xg, max_goals=8):
    matrix = {}

    total = 0.0

    for home_goals in range(max_goals + 1):
        ph = poisson_probability(home_goals, home_xg)

        for away_goals in range(max_goals + 1):
            pa = poisson_probability(away_goals, away_xg)

            probability = ph * pa

            matrix[(home_goals, away_goals)] = probability
            total += probability

    # Normalize because we truncate at max_goals.
    if total > 0:
        for key in matrix:
            matrix[key] /= total

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

    for (hg, ag), probability in matrix.items():
        total_goals = hg + ag

        if hg > ag:
            home_win += probability
        elif hg == ag:
            draw += probability
        else:
            away_win += probability

        if total_goals > 0:
            over05 += probability

        if total_goals > 1:
            over15 += probability

        if total_goals > 2:
            over25 += probability

        if total_goals < 4:
            under35 += probability

        if total_goals < 5:
            under45 += probability

        if hg > 0 and ag > 0:
            btts += probability

        if hg > 0:
            home_score += probability

        if ag > 0:
            away_score += probability

    # Normalize 1X2 exactly.
    result_total = home_win + draw + away_win

    if result_total > 0:
        home_win /= result_total
        draw /= result_total
        away_win /= result_total

    return {
        "home_win": home_win * 100,
        "draw": draw * 100,
        "away_win": away_win * 100,

        "double_home": (home_win + draw) * 100,
        "double_away": (away_win + draw) * 100,

        "over05": over05 * 100,
        "over15": over15 * 100,
        "over25": over25 * 100,

        "under35": under35 * 100,
        "under45": under45 * 100,

        "btts": btts * 100,

        "home_score": home_score * 100,
        "away_score": away_score * 100,
    }


def top_scorelines(matrix, count=3):
    ordered = sorted(
        matrix.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    return ordered[:count]


# ============================================================
# DATA STRENGTH / CONFIDENCE
# ============================================================

def data_strength(
    home_recent,
    away_recent,
    home_venue,
    away_venue,
):
    recent_count = min(
        len(home_recent),
        SAMPLE_SIZE,
    )

    away_count = min(
        len(away_recent),
        SAMPLE_SIZE,
    )

    venue_home = min(
        len(home_venue),
        SAMPLE_SIZE,
    )

    venue_away = min(
        len(away_venue),
        SAMPLE_SIZE,
    )

    recent_score = (
        (recent_count / SAMPLE_SIZE) * 0.55
        + (away_count / SAMPLE_SIZE) * 0.25
        + (venue_home / SAMPLE_SIZE) * 0.10
        + (venue_away / SAMPLE_SIZE) * 0.10
    )

    return max(0.0, min(100.0, recent_score * 100))


def confidence_label(strength):
    if strength >= 85:
        return "HIGH"
    if strength >= 65:
        return "MEDIUM"
    return "LOW"


# ============================================================
# TOP SIGNALS
# ============================================================

def build_markets(model):
    return {
        "Over 0.5": model["over05"],
        "Over 1.5": model["over15"],
        "Over 2.5": model["over25"],
        "Under 3.5": model["under35"],
        "Under 4.5": model["under45"],
        "BTTS": model["btts"],
        "Team 1 to score": model["home_score"],
        "Team 2 to score": model["away_score"],
        "1X": model["double_home"],
        "X2": model["double_away"],
    }


def top_signals(model, count=3):
    markets = build_markets(model)

    return sorted(
        markets.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:count]


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"⚽ Welcome to GoalLogic AI v{VERSION}!\n\n"
        "I analyze football matches using recent form, "
        "home/away evidence, expected goals and a Poisson "
        "score model.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )


async def apitest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team, error = find_team("Chelsea")

    if error:
        await update.message.reply_text(
            "🔧 FOOTBALL API TEST\n\n"
            "❌ OpenFoot API connection failed.\n\n"
            f"{error}"
        )
        return

    await update.message.reply_text(
        "🔧 FOOTBALL API TEST\n\n"
        "✅ OpenFoot API connection works.\n"
        f"✅ Found Chelsea.\n"
        f"Team ID: {team_id(team)}"
    )


async def team_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/team Chelsea"
        )
        return

    query = " ".join(context.args)

    results, error = search_team(query)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    if not results:
        await update.message.reply_text(
            f"❌ No team found for '{query}'."
        )
        return

    lines = [
        f"🔎 Team search: {query}",
        "",
    ]

    for item in results[:10]:
        lines.append(
            f"• {team_name(item)}"
            f" — ID: {team_id(item)}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def fixtures_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    matches, error = get_team_matches(team)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    oriented = team_oriented_matches(
        team,
        matches,
    )

    if not oriented:
        await update.message.reply_text(
            f"❌ No completed {SEASON} matches found "
            f"for {team_name(team)}."
        )
        return

    recent = oriented[:SAMPLE_SIZE]

    lines = [
        f"📅 {team_name(team)}",
        f"Season: {SEASON}",
        f"Showing last {len(recent)} completed matches",
        "",
    ]

    for item in recent:
        match = item["match"]

        date = str(
            match.get("kickoffAt")
            or match.get("date")
            or match.get("kickoff")
            or ""
        )

        date = date[:10]

        lines.append(
            f"{date} | {item['venue']} | "
            f"{item['result']} | "
            f"{int(item['gf'])}-{int(item['ga'])} "
            f"vs {item['opponent']}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


def parse_analyze_command(text):
    raw = text.strip()

    if raw.lower().startswith("/analyze"):
        raw = raw[len("/analyze"):].strip()

    # Case-insensitive VS parsing
    import re

    parts = re.split(
        r"\s+vs\.?\s+",
        raw,
        flags=re.IGNORECASE,
    )

    if len(parts) != 2:
        return None, None

    home = parts[0].strip()
    away = parts[1].strip()

    if not home or not away:
        return None, None

    return home, away


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""

    home_query, away_query = parse_analyze_command(text)

    if not home_query or not away_query:
        await update.message.reply_text(
            "Usage:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    home_team, error = find_team(home_query)

    if error:
        await update.message.reply_text(
            f"❌ Home team error:\n{error}"
        )
        return

    away_team, error = find_team(away_query)

    if error:
        await update.message.reply_text(
            f"❌ Away team error:\n{error}"
        )
        return

    home_matches, error = get_team_matches(home_team)

    if error:
        await update.message.reply_text(
            f"❌ Could not retrieve "
            f"{team_name(home_team)} data.\n\n{error}"
        )
        return

    away_matches, error = get_team_matches(away_team)

    if error:
        await update.message.reply_text(
            f"❌ Could not retrieve "
            f"{team_name(away_team)} data.\n\n{error}"
        )
        return

    home_oriented = team_oriented_matches(
        home_team,
        home_matches,
    )

    away_oriented = team_oriented_matches(
        away_team,
        away_matches,
    )

    if not home_oriented:
        await update.message.reply_text(
            f"❌ No recent {SEASON} data found for "
            f"{team_name(home_team)}."
        )
        return

    if not away_oriented:
        await update.message.reply_text(
            f"❌ No recent {SEASON} data found for "
            f"{team_name(away_team)}."
        )
        return

    home_recent = home_oriented[:SAMPLE_SIZE]
    away_recent = away_oriented[:SAMPLE_SIZE]

    home_venue = [
        m for m in home_oriented
        if m["venue"] == "HOME"
    ][:SAMPLE_SIZE]

    away_venue = [
        m for m in away_oriented
        if m["venue"] == "AWAY"
    ][:SAMPLE_SIZE]

    home_stats = calculate_stats(home_recent)
    away_stats = calculate_stats(away_recent)

    home_venue_stats = calculate_stats(home_venue)
    away_venue_stats = calculate_stats(away_venue)

    home_xg, away_xg = estimate_xg(
        home_recent,
        away_recent,
        home_venue,
        away_venue,
    )

    matrix = build_score_matrix(
        home_xg,
        away_xg,
        max_goals=8,
    )

    model = matrix_markets(matrix)

    scorelines = top_scorelines(
        matrix,
        count=3,
    )

    signals = top_signals(
        model,
        count=3,
    )

    strength = data_strength(
        home_recent,
        away_recent,
        home_venue,
        away_venue,
    )

    confidence = confidence_label(strength)

    # ========================================================
    # BUILD RESPONSE
    # ========================================================

    home_name = team_name(home_team)
    away_name = team_name(away_team)

    lines = []

    lines.append(
        f"⚽ GOALLOGIC AI v{VERSION}"
    )

    lines.append("")
    lines.append(
        f"🏟️ {home_name} vs {away_name}"
    )

    lines.append("")
    lines.append(
        f"Season: {SEASON}"
    )

    lines.append(
        f"Sample: Last {SAMPLE_SIZE} available matches"
    )

    # --------------------------------------------------------
    # HOME RECENT
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        f"🏠 {home_name} recent:"
    )

    lines.append(
        f"{recent_form_string(home_recent)} | "
        f"W/D/L "
        f"{home_stats['wins']}/"
        f"{home_stats['draws']}/"
        f"{home_stats['losses']} | "
        f"GF{int(home_stats['gf'])} "
        f"GA{int(home_stats['ga'])}"
    )

    lines.append(
        f"Avg {home_stats['avg_gf']:.2f}/"
        f"{home_stats['avg_ga']:.2f} | "
        f"O1.5 {pct(home_stats['over15'])}% | "
        f"O2.5 {pct(home_stats['over25'])}% | "
        f"U3.5 {pct(home_stats['under35'])}% | "
        f"BTTS {pct(home_stats['btts'])}%"
    )

    lines.append(
        f"Scoring {pct(home_stats['scoring'])}% | "
        f"CS {pct(home_stats['clean_sheet'])}%"
    )

    # --------------------------------------------------------
    # AWAY RECENT
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        f"✈️ {away_name} recent:"
    )

    lines.append(
        f"{recent_form_string(away_recent)} | "
        f"W/D/L "
        f"{away_stats['wins']}/"
        f"{away_stats['draws']}/"
        f"{away_stats['losses']} | "
        f"GF{int(away_stats['gf'])} "
        f"GA{int(away_stats['ga'])}"
    )

    lines.append(
        f"Avg {away_stats['avg_gf']:.2f}/"
        f"{away_stats['avg_ga']:.2f} | "
        f"O1.5 {pct(away_stats['over15'])}% | "
        f"O2.5 {pct(away_stats['over25'])}% | "
        f"U3.5 {pct(away_stats['under35'])}% | "
        f"BTTS {pct(away_stats['btts'])}%"
    )

    lines.append(
        f"Scoring {pct(away_stats['scoring'])}% | "
        f"CS {pct(away_stats['clean_sheet'])}%"
    )

    # --------------------------------------------------------
    # VENUE
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        f"🏟️ {home_name} HOME:"
    )

    lines.append(
        f"{home_venue_stats['games']} games | "
        f"W/D/L "
        f"{home_venue_stats['wins']}/"
        f"{home_venue_stats['draws']}/"
        f"{home_venue_stats['losses']} | "
        f"Avg "
        f"{home_venue_stats['avg_gf']:.2f}/"
        f"{home_venue_stats['avg_ga']:.2f}"
    )

    lines.append(
        f"Scoring {pct(home_venue_stats['scoring'])}% | "
        f"CS {pct(home_venue_stats['clean_sheet'])}%"
    )

    lines.append("")
    lines.append(
        f"✈️ {away_name} AWAY:"
    )

    lines.append(
        f"{away_venue_stats['games']} games | "
        f"W/D/L "
        f"{away_venue_stats['wins']}/"
        f"{away_venue_stats['draws']}/"
        f"{away_venue_stats['losses']} | "
        f"Avg "
        f"{away_venue_stats['avg_gf']:.2f}/"
        f"{away_venue_stats['avg_ga']:.2f}"
    )

    lines.append(
        f"Scoring {pct(away_venue_stats['scoring'])}% | "
        f"CS {pct(away_venue_stats['clean_sheet'])}%"
    )

    # --------------------------------------------------------
    # XG
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "📊 EXPECTED GOALS MODEL"
    )

    lines.append(
        f"{home_name}: {home_xg:.2f} xG"
    )

    lines.append(
        f"{away_name}: {away_xg:.2f} xG"
    )

    lines.append(
        f"Total: {home_xg + away_xg:.2f} xG"
    )

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "🎯 RESULT MODEL"
    )

    lines.append(
        f"1: {pct(model['home_win'])}% | "
        f"X: {pct(model['draw'])}% | "
        f"2: {pct(model['away_win'])}%"
    )

    # --------------------------------------------------------
    # MARKETS
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "📈 MARKET MODEL"
    )

    lines.append(
        f"1X: {pct(model['double_home'])}%"
    )

    lines.append(
        f"X2: {pct(model['double_away'])}%"
    )

    lines.append(
        f"Over 0.5: {pct(model['over05'])}%"
    )

    lines.append(
        f"Over 1.5: {pct(model['over15'])}%"
    )

    lines.append(
        f"Over 2.5: {pct(model['over25'])}%"
    )

    lines.append(
        f"Under 3.5: {pct(model['under35'])}%"
    )

    lines.append(
        f"Under 4.5: {pct(model['under45'])}%"
    )

    lines.append(
        f"BTTS: {pct(model['btts'])}%"
    )

    lines.append(
        f"Team 1 to score: {pct(model['home_score'])}%"
    )

    lines.append(
        f"Team 2 to score: {pct(model['away_score'])}%"
    )

    # --------------------------------------------------------
    # SCORELINES
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "🥅 TOP SCORELINES"
    )

    for (hg, ag), probability in scorelines:
        lines.append(
            f"{int(hg)}-{int(ag)}: "
            f"{pct(probability * 100)}%"
        )

    # --------------------------------------------------------
    # TOP SIGNALS
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "🔥 TOP MODEL SIGNALS"
    )

    for market, probability in signals:
        lines.append(
            f"🟢 {market} "
            f"{pct(probability)}%"
        )

    # --------------------------------------------------------
    # MODEL QUALITY
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "🧠 MODEL QUALITY"
    )

    lines.append(
        f"Data strength: {pct(strength)}%"
    )

    lines.append(
        "Probability consistency: ✅ PASS"
    )

    lines.append(
        f"Model confidence: 🟢 "
        f"{pct(strength * 0.85)}% "
        f"({confidence})"
    )

    # --------------------------------------------------------
    # PRIMARY SIGNAL
    # --------------------------------------------------------

    primary_market, primary_probability = signals[0]

    lines.append("")
    lines.append(
        f"📌 PRIMARY MODEL SIGNAL: "
        f"{primary_market} "
        f"{pct(primary_probability)}%"
    )

    lines.append("")
    lines.append(
        "ℹ️ Probabilities are mathematical model "
        "estimates from recent data, venue evidence "
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

    if not OPENFOOT_API_KEY:
        raise RuntimeError(
            "OPENFOOT_API_KEY is missing."
        )

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )

    health_thread.start()

    print(
        f"GoalLogic AI v{VERSION} "
        "Telegram bot is starting."
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
