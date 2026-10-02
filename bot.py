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


# =========================================================
# GOALLOGIC AI v3.1
# =========================================================

VERSION = "3.1"

API_BASE = "https://openfootapi.com/v1"
SEASON = "2026/27"
SAMPLE_SIZE = 5

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY")

PORT = int(os.getenv("PORT", "10000"))


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
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


# =========================================================
# API
# =========================================================

def api_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        return None

    url = f"{API_BASE}/{endpoint.lstrip('/')}"

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
            print(
                f"API error {response.status_code}: "
                f"{response.text[:500]}"
            )
            return None

        return response.json()

    except Exception as e:
        print(f"API request error: {e}")
        return None


# =========================================================
# TEAM SEARCH
# =========================================================

def find_team(query):
    data = api_get("search", {"q": query})

    if not data:
        return None

    teams = data.get("data", [])

    if not teams:
        return None

    query_lower = query.lower().strip()

    # Exact name match first
    for team in teams:
        name = str(team.get("name", "")).strip()

        if name.lower() == query_lower:
            return team

    # Partial match
    for team in teams:
        name = str(team.get("name", "")).strip()

        if query_lower in name.lower():
            return team

    return teams[0]


# =========================================================
# BASIC HELPERS
# =========================================================

def team_id(team):
    return str(team.get("id", ""))


def team_name(team):
    return str(team.get("name", "Unknown Team"))


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


# =========================================================
# SCORE EXTRACTION
# =========================================================

def extract_score_pair(match):
    """
    Tries several possible OpenFoot score structures.
    Returns:
        (home_goals, away_goals)
    or:
        None
    """

    possible_pairs = [
        ("homeScore", "awayScore"),
        ("homeGoals", "awayGoals"),
        ("home_score", "away_score"),
        ("homeGoalsScored", "awayGoalsScored"),
    ]

    for home_key, away_key in possible_pairs:
        if home_key in match and away_key in match:
            h = match.get(home_key)
            a = match.get(away_key)

            if h is not None and a is not None:
                try:
                    return int(h), int(a)
                except Exception:
                    pass

    # Nested score object
    score = match.get("score")

    if isinstance(score, dict):

        # score.home / score.away
        h = score.get("home")
        a = score.get("away")

        if h is not None and a is not None:
            try:
                return int(h), int(a)
            except Exception:
                pass

        # score.fulltime.home / away
        fulltime = score.get("fulltime")

        if isinstance(fulltime, dict):
            h = fulltime.get("home")
            a = fulltime.get("away")

            if h is not None and a is not None:
                try:
                    return int(h), int(a)
                except Exception:
                    pass

        # score.final.home / away
        final = score.get("final")

        if isinstance(final, dict):
            h = final.get("home")
            a = final.get("away")

            if h is not None and a is not None:
                try:
                    return int(h), int(a)
                except Exception:
                    pass

    # Nested teams with scores
    home_team = match.get("homeTeam")
    away_team = match.get("awayTeam")

    if isinstance(home_team, dict) and isinstance(away_team, dict):

        h = (
            home_team.get("score")
            if home_team.get("score") is not None
            else home_team.get("goals")
        )

        a = (
            away_team.get("score")
            if away_team.get("score") is not None
            else away_team.get("goals")
        )

        if h is not None and a is not None:
            try:
                return int(h), int(a)
            except Exception:
                pass

    return None


# =========================================================
# MATCH HELPERS
# =========================================================

def match_team_name(match, side):
    obj = match.get(f"{side}Team")

    if isinstance(obj, dict):
        return str(obj.get("name", ""))

    return ""


def match_team_id(match, side):
    obj = match.get(f"{side}Team")

    if isinstance(obj, dict):
        return str(obj.get("id", ""))

    return ""


def is_completed_match(match):
    score = extract_score_pair(match)

    if score is None:
        return False

    status = str(match.get("status", "")).lower()

    unfinished_statuses = {
        "scheduled",
        "upcoming",
        "pending",
        "not_started",
        "not started",
        "postponed",
        "cancelled",
        "canceled",
    }

    if status in unfinished_statuses:
        return False

    return True


def get_team_matches(team):
    tid = team_id(team)

    data = api_get(
        "matches",
        {
            "team": tid,
            "season": SEASON,
        },
    )

    if not data:
        return []

    matches = data.get("data", [])

    completed = [
        m for m in matches
        if is_completed_match(m)
    ]

    # Sort newest first
    completed.sort(
        key=lambda x: str(
            x.get("kickoffAt", "")
        ),
        reverse=True,
    )

    return completed


def orient_match(match, tid):
    home_id = match_team_id(match, "home")
    away_id = match_team_id(match, "away")

    score = extract_score_pair(match)

    if score is None:
        return None

    hg, ag = score

    if home_id == tid:
        return {
            "venue": "HOME",
            "goals_for": hg,
            "goals_against": ag,
            "opponent": match_team_name(match, "away"),
            "date": str(match.get("kickoffAt", ""))[:10],
        }

    if away_id == tid:
        return {
            "venue": "AWAY",
            "goals_for": ag,
            "goals_against": hg,
            "opponent": match_team_name(match, "home"),
            "date": str(match.get("kickoffAt", ""))[:10],
        }

    return None


# =========================================================
# STATS
# =========================================================

def calculate_stats(matches):
    if not matches:
        return {
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "avg_gf": 0,
            "avg_ga": 0,
            "o15": 0,
            "o25": 0,
            "u35": 0,
            "btts": 0,
            "scoring": 0,
            "clean_sheet": 0,
        }

    games = len(matches)

    wins = draws = losses = 0
    gf = ga = 0

    o15 = o25 = u35 = btts = 0
    scoring = clean_sheet = 0

    for m in matches:
        f = m["goals_for"]
        a = m["goals_against"]

        gf += f
        ga += a

        if f > a:
            wins += 1
        elif f == a:
            draws += 1
        else:
            losses += 1

        total = f + a

        if total > 1:
            o15 += 1

        if total > 2:
            o25 += 1

        if total < 4:
            u35 += 1

        if f > 0 and a > 0:
            btts += 1

        if f > 0:
            scoring += 1

        if a == 0:
            clean_sheet += 1

    pct = lambda x: round((x / games) * 100)

    return {
        "games": games,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf,
        "ga": ga,
        "avg_gf": gf / games,
        "avg_ga": ga / games,
        "o15": pct(o15),
        "o25": pct(o25),
        "u35": pct(u35),
        "btts": pct(btts),
        "scoring": pct(scoring),
        "clean_sheet": pct(clean_sheet),
    }


def filter_venue(matches, venue):
    return [
        m for m in matches
        if m["venue"] == venue
    ]


# =========================================================
# XG MODEL
# =========================================================

def estimate_xg(recent, venue, opponent_venue):
    """
    Heuristic model-estimated xG.
    It is NOT provider xG.
    """

    if not recent:
        return 1.20

    recent_avg_for = recent["avg_gf"]
    recent_avg_against = recent["avg_ga"]

    if venue and venue["games"] > 0:
        venue_attack = venue["avg_gf"]
        venue_defense = venue["avg_ga"]
    else:
        venue_attack = recent_avg_for
        venue_defense = recent_avg_ga

    if opponent_venue and opponent_venue["games"] > 0:
        opp_defense = opponent_venue["avg_ga"]
    else:
        opp_defense = 1.20

    attack_component = (
        0.50 * recent_avg_for
        + 0.30 * venue_attack
        + 0.20 * opp_defense
    )

    defensive_component = (
        0.60 * recent_avg_against
        + 0.40 * venue_defense
    )

    xg = (
        0.60 * attack_component
        + 0.40 * defensive_component
    )

    return clamp(xg, 0.25, 3.50)


# =========================================================
# POISSON
# =========================================================

def poisson_pmf(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0

    return (
        math.exp(-lam)
        * (lam ** k)
        / math.factorial(k)
    )


def build_poisson_matrix(home_xg, away_xg, max_goals=8):
    matrix = {}

    total = 0.0

    for h in range(max_goals + 1):
        for a in range(max_goals + 1):

            p = (
                poisson_pmf(h, home_xg)
                * poisson_pmf(a, away_xg)
            )

            matrix[(h, a)] = p
            total += p

    # Normalize because the matrix is truncated at max_goals.
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

    for (h, a), p in matrix.items():

        if h > a:
            home_win += p
        elif h == a:
            draw += p
        else:
            away_win += p

        total = h + a

        if total > 0:
            over05 += p

        if total > 1:
            over15 += p

        if total > 2:
            over25 += p

        if total < 4:
            under35 += p

        if total < 5:
            under45 += p

        if h > 0 and a > 0:
            btts += p

        if h > 0:
            home_score += p

        if a > 0:
            away_score += p

    result_total = home_win + draw + away_win

    if result_total > 0:
        home_win /= result_total
        draw /= result_total
        away_win /= result_total

    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "1x": home_win + draw,
        "x2": draw + away_win,
        "over05": over05,
        "over15": over15,
        "over25": over25,
        "under35": under35,
        "under45": under45,
        "btts": btts,
        "home_score": home_score,
        "away_score": away_score,
    }


# =========================================================
# TOP SCORELINES
# =========================================================

def top_scorelines(matrix, count=3):
    ordered = sorted(
        matrix.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    return ordered[:count]


# =========================================================
# GOAL DISTRIBUTION
# =========================================================

def goal_distribution(matrix):
    distribution = {}

    for (h, a), p in matrix.items():
        total = h + a
        distribution[total] = (
            distribution.get(total, 0.0) + p
        )

    return distribution


def format_goal_distribution(matrix):
    dist = goal_distribution(matrix)

    parts = []

    for goals in range(0, 6):
        p = dist.get(goals, 0.0) * 100
        parts.append(f"{goals}: {p:.0f}%")

    return " | ".join(parts)


# =========================================================
# DATA STRENGTH
# =========================================================

def calculate_data_strength(
    home_recent_count,
    away_recent_count,
    home_venue_count,
    away_venue_count,
):
    """
    Conservative data-strength calculation.

    Recent sample max contribution:
    60 points

    Venue sample max contribution:
    40 points
    """

    recent_score = (
        min(home_recent_count, SAMPLE_SIZE) / SAMPLE_SIZE
        + min(away_recent_count, SAMPLE_SIZE) / SAMPLE_SIZE
    ) / 2

    venue_target = 5

    venue_score = (
        min(home_venue_count, venue_target) / venue_target
        + min(away_venue_count, venue_target) / venue_target
    ) / 2

    strength = (
        60 * recent_score
        + 40 * venue_score
    )

    return round(clamp(strength, 0, 100))


# =========================================================
# CONFIDENCE CALIBRATION
# =========================================================

def calculate_model_confidence(
    data_strength,
    markets,
):
    """
    Conservative confidence.

    This does NOT mean probability of a bet winning.
    It is a measure of how much trust the model places
    in its current analysis.
    """

    strongest = max(
        markets["over05"],
        markets["over15"],
        markets["over25"],
        markets["under35"],
        markets["under45"],
        markets["btts"],
        markets["home_score"],
        markets["away_score"],
        markets["1x"],
        markets["x2"],
    )

    # Distance from 50%.
    signal_strength = abs(strongest - 0.50)

    signal_component = clamp(
        50 + (signal_strength * 100),
        50,
        90,
    )

    confidence = (
        0.55 * data_strength
        + 0.45 * signal_component
    )

    # Conservative cap for small samples.
    if data_strength < 60:
        confidence *= 0.82
    elif data_strength < 75:
        confidence *= 0.90
    elif data_strength < 85:
        confidence *= 0.95

    return round(
        clamp(confidence, 50, 90)
    )


# =========================================================
# PROBABILITY AUDIT
# =========================================================

def probability_audit(markets):
    result_sum = (
        markets["home_win"]
        + markets["draw"]
        + markets["away_win"]
    )

    checks = [
        abs(result_sum - 1.0) <= 0.01,
        abs(
            markets["1x"]
            - (
                markets["home_win"]
                + markets["draw"]
            )
        ) <= 0.01,
        abs(
            markets["x2"]
            - (
                markets["draw"]
                + markets["away_win"]
            )
        ) <= 0.01,
        markets["over05"] >= markets["over15"],
        markets["over15"] >= markets["over25"],
        markets["under35"] <= markets["under45"],
        markets["btts"] <= markets["home_score"],
        markets["btts"] <= markets["away_score"],
    ]

    return all(checks)


# =========================================================
# SIGNALS
# =========================================================

def get_top_signals(markets):
    signals = {
        "Over 0.5": markets["over05"],
        "Over 1.5": markets["over15"],
        "Over 2.5": markets["over25"],
        "Under 3.5": markets["under35"],
        "Under 4.5": markets["under45"],
        "BTTS": markets["btts"],
        "Team 1 to score": markets["home_score"],
        "Team 2 to score": markets["away_score"],
        "1X": markets["1x"],
        "X2": markets["x2"],
    }

    return sorted(
        signals.items(),
        key=lambda x: x[1],
        reverse=True,
    )


# =========================================================
# FORMAT STATS
# =========================================================

def format_recent_stats(name, stats, icon):
    form = (
        "W" * stats["wins"]
        + "D" * stats["draws"]
        + "L" * stats["losses"]
    )

    return (
        f"{icon} {name} recent:\n"
        f"{form} | "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']} "
        f"| GF{stats['gf']} GA{stats['ga']}\n"
        f"Avg {stats['avg_gf']:.2f}/{stats['avg_ga']:.2f} "
        f"| O1.5 {stats['o15']}% "
        f"| O2.5 {stats['o25']}% "
        f"| U3.5 {stats['u35']}% "
        f"| BTTS {stats['btts']}%\n"
        f"Scoring {stats['scoring']}% "
        f"| CS {stats['clean_sheet']}%"
    )


# =========================================================
# ANALYSIS
# =========================================================

def analyze_match(home_team, away_team):
    home_matches_raw = get_team_matches(home_team)
    away_matches_raw = get_team_matches(away_team)

    home_tid = team_id(home_team)
    away_tid = team_id(away_team)

    home_oriented = []

    for match in home_matches_raw:
        item = orient_match(match, home_tid)

        if item:
            home_oriented.append(item)

    away_oriented = []

    for match in away_matches_raw:
        item = orient_match(match, away_tid)

        if item:
            away_oriented.append(item)

    home_recent = home_oriented[:SAMPLE_SIZE]
    away_recent = away_oriented[:SAMPLE_SIZE]

    if not home_recent:
        return (
            f"❌ No recent {SEASON} data found for "
            f"{team_name(home_team)}."
        )

    if not away_recent:
        return (
            f"❌ No recent {SEASON} data found for "
            f"{team_name(away_team)}."
        )

    home_recent_stats = calculate_stats(home_recent)
    away_recent_stats = calculate_stats(away_recent)

    home_venue_matches = filter_venue(
        home_oriented,
        "HOME",
    )[:SAMPLE_SIZE]

    away_venue_matches = filter_venue(
        away_oriented,
        "AWAY",
    )[:SAMPLE_SIZE]

    home_venue_stats = calculate_stats(
        home_venue_matches
    )

    away_venue_stats = calculate_stats(
        away_venue_matches
    )

    # Model-estimated xG
    home_xg = estimate_xg(
        home_recent_stats,
        home_venue_stats,
        away_venue_stats,
    )

    away_xg = estimate_xg(
        away_recent_stats,
        away_venue_stats,
        home_venue_stats,
    )

    matrix = build_poisson_matrix(
        home_xg,
        away_xg,
        max_goals=8,
    )

    markets = matrix_markets(matrix)

    scorelines = top_scorelines(
        matrix,
        count=3,
    )

    data_strength = calculate_data_strength(
        len(home_recent),
        len(away_recent),
        len(home_venue_matches),
        len(away_venue_matches),
    )

    confidence = calculate_model_confidence(
        data_strength,
        markets,
    )

    consistency = probability_audit(
        markets
    )

    top_signals = get_top_signals(markets)

    # Primary signal
    primary_name, primary_probability = top_signals[0]

    # Prevent weak signals from being presented as strong.
    if primary_probability < 0.60:
        primary_signal = "No strong model signal"
    else:
        primary_signal = (
            f"{primary_name} "
            f"{primary_probability * 100:.0f}%"
        )

    quality_label = "🟢 HIGH"

    if confidence < 65:
        quality_label = "🟡 MODERATE"
    elif confidence < 75:
        quality_label = "🟡 GOOD"

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
        format_recent_stats(
            team_name(home_team),
            home_recent_stats,
            "🏠",
        )
    )

    lines.append("")

    lines.append(
        format_recent_stats(
            team_name(away_team),
            away_recent_stats,
            "✈️",
        )
    )

    lines.append("")

    lines.append(
        f"🏟️ {team_name(home_team)} HOME:"
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
        f"Scoring {home_venue_stats['scoring']}% "
        f"| CS {home_venue_stats['clean_sheet']}%"
    )

    lines.append("")

    lines.append(
        f"✈️ {team_name(away_team)} AWAY:"
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
        f"Scoring {away_venue_stats['scoring']}% "
        f"| CS {away_venue_stats['clean_sheet']}%"
    )

    lines.append("")

    lines.append(
        "📊 EXPECTED GOALS MODEL"
    )

    lines.append(
        f"{team_name(home_team)}: "
        f"{home_xg:.2f} xG"
    )

    lines.append(
        f"{team_name(away_team)}: "
        f"{away_xg:.2f} xG"
    )

    lines.append(
        f"Total: "
        f"{home_xg + away_xg:.2f} xG"
    )

    lines.append("")

    lines.append("🎯 RESULT MODEL")

    lines.append(
        f"1: {markets['home_win'] * 100:.0f}% "
        f"| X: {markets['draw'] * 100:.0f}% "
        f"| 2: {markets['away_win'] * 100:.0f}%"
    )

    lines.append("")

    lines.append("📈 MARKET MODEL")

    lines.append(
        f"1X: {markets['1x'] * 100:.0f}%"
    )

    lines.append(
        f"X2: {markets['x2'] * 100:.0f}%"
    )

    lines.append(
        f"Over 0.5: {markets['over05'] * 100:.0f}%"
    )

    lines.append(
        f"Over 1.5: {markets['over15'] * 100:.0f}%"
    )

    lines.append(
        f"Over 2.5: {markets['over25'] * 100:.0f}%"
    )

    lines.append(
        f"Under 3.5: {markets['under35'] * 100:.0f}%"
    )

    lines.append(
        f"Under 4.5: {markets['under45'] * 100:.0f}%"
    )

    lines.append(
        f"BTTS: {markets['btts'] * 100:.0f}%"
    )

    lines.append(
        f"Team 1 to score: "
        f"{markets['home_score'] * 100:.0f}%"
    )

    lines.append(
        f"Team 2 to score: "
        f"{markets['away_score'] * 100:.0f}%"
    )

    lines.append("")

    lines.append("🥅 TOP SCORELINES")

    for (h, a), p in scorelines:
        lines.append(
            f"{h}-{a}: {p * 100:.0f}%"
        )

    lines.append("")

    lines.append("⚽ GOAL DISTRIBUTION")

    lines.append(
        format_goal_distribution(matrix)
    )

    lines.append(
        "0 / 1 / 2 / 3 / 4 / 5+ goals"
    )

    lines.append("")

    lines.append("🔥 TOP MODEL SIGNALS")

    for name, probability in top_signals[:3]:

        if probability >= 0.75:
            emoji = "🟢"
        elif probability >= 0.65:
            emoji = "🟡"
        else:
            emoji = "⚪"

        lines.append(
            f"{emoji} {name} "
            f"{probability * 100:.0f}%"
        )

    lines.append("")

    lines.append("🧠 MODEL QUALITY")

    lines.append(
        f"Data strength: {data_strength}%"
    )

    lines.append(
        "Probability consistency: "
        + ("✅ PASS" if consistency else "❌ CHECK")
    )

    lines.append(
        f"Model confidence: "
        f"{quality_label} {confidence}%"
    )

    lines.append("")

    lines.append(
        f"📌 PRIMARY MODEL SIGNAL: "
        f"{primary_signal}"
    )

    lines.append("")

    lines.append(
        "ℹ️ Probabilities are mathematical model "
        "estimates from recent data, venue evidence "
        "and a Poisson score model. They are not "
        "guarantees."
    )

    return "\n".join(lines)


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        f"⚽ Welcome to GoalLogic AI v{VERSION}!\n\n"
        "I analyze football matches using recent form, "
        "home/away evidence, model-estimated xG and "
        "a Poisson score model.\n\n"
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
    data = api_get(
        "search",
        {"q": "Chelsea"},
    )

    if not data:
        await update.message.reply_text(
            "❌ OpenFoot API connection failed."
        )
        return

    team = find_team("Chelsea")

    if not team:
        await update.message.reply_text(
            "⚠️ API connected, but Chelsea was not found."
        )
        return

    await update.message.reply_text(
        "🔧 FOOTBALL API TEST\n\n"
        "OpenFoot API connection works.\n"
        "Found Chelsea.\n"
        f"Team ID: {team_id(team)}"
    )


async def team_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/team Chelsea"
        )
        return

    query = " ".join(context.args)

    data = api_get(
        "search",
        {"q": query},
    )

    if not data:
        await update.message.reply_text(
            "❌ Team search failed."
        )
        return

    teams = data.get("data", [])

    if not teams:
        await update.message.reply_text(
            f"❌ No teams found for: {query}"
        )
        return

    lines = [
        f"🔎 Team search: {query}",
        "",
    ]

    for team in teams[:10]:
        lines.append(
            f"⚽ {team_name(team)}"
        )
        lines.append(
            f"ID: {team_id(team)}"
        )

        country = team.get("country")

        if country:
            lines.append(
                f"Country: {country}"
            )

        lines.append("")

    await update.message.reply_text(
        "\n".join(lines)
    )


async def fixtures_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/fixtures Chelsea"
        )
        return

    query = " ".join(context.args)

    team = find_team(query)

    if not team:
        await update.message.reply_text(
            f"❌ Team not found: {query}"
        )
        return

    matches_raw = get_team_matches(team)

    tid = team_id(team)

    oriented = []

    for match in matches_raw:
        item = orient_match(match, tid)

        if item:
            oriented.append(item)

    oriented = oriented[:SAMPLE_SIZE]

    if not oriented:
        await update.message.reply_text(
            f"❌ No completed {SEASON} matches "
            f"found for {team_name(team)}."
        )
        return

    lines = [
        f"📅 {team_name(team)}",
        f"Season: {SEASON}",
        f"Showing last {len(oriented)} completed matches",
        "",
    ]

    for item in oriented:

        result = (
            "W"
            if item["goals_for"] > item["goals_against"]
            else "D"
            if item["goals_for"] == item["goals_against"]
            else "L"
        )

        lines.append(
            f"{item['date']} | "
            f"{item['venue']} | "
            f"{result} | "
            f"{item['goals_for']}-{item['goals_against']} "
            f"vs {item['opponent']}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/analyze Chelsea vs Arsenal"
        )
        return

    text = " ".join(context.args)

    if " vs " in text.lower():
        parts = text.lower().split(" vs ", 1)

        # Recover original capitalization from args.
        original = " ".join(context.args)

        separator_index = original.lower().find(" vs ")

        home_query = original[:separator_index].strip()
        away_query = original[
            separator_index + 4:
        ].strip()

    else:
        await update.message.reply_text(
            "❌ Please use:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    if not home_query or not away_query:
        await update.message.reply_text(
            "❌ Please provide two teams.\n"
            "Example:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    home_team = find_team(home_query)
    away_team = find_team(away_query)

    if not home_team:
        await update.message.reply_text(
            f"❌ Team not found: {home_query}"
        )
        return

    if not away_team:
        await update.message.reply_text(
            f"❌ Team not found: {away_query}"
        )
        return

    await update.message.reply_text(
        "🔎 Analyzing match...\n"
        "Please wait."
    )

    try:
        result = analyze_match(
            home_team,
            away_team,
        )

        await update.message.reply_text(
            result
        )

    except Exception as e:
        print(
            f"Analysis error: {e}"
        )

        await update.message.reply_text(
            "❌ Analysis failed.\n"
            "Please try again."
        )


# =========================================================
# MAIN
# =========================================================

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

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "apitest",
            apitest_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "team",
            team_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "fixtures",
            fixtures_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "analyze",
            analyze_command,
        )
    )

    print(
        f"GoalLogic AI v{VERSION} "
        "Telegram bot is starting."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
