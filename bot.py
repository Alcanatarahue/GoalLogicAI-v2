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
# GOALLOGIC AI v2.7
# ============================================================

VERSION = "2.7"

API_BASE = "https://openfootapi.com/v1"
SEASON = "2026/27"
SAMPLE_SIZE = 5

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.environ.get("OPENFOOT_API_KEY")

PORT = int(os.environ.get("PORT", "10000"))


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

def api_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        raise Exception("OPENFOOT_API_KEY is missing.")

    headers = {
        "Authorization": f"Bearer {OPENFOOT_API_KEY}",
        "Accept": "application/json",
    }

    url = f"{API_BASE}{endpoint}"

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=20,
    )

    if response.status_code != 200:
        raise Exception(
            f"OpenFoot API error {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    if isinstance(data, dict):
        return data.get("data", data)

    return data


# ============================================================
# TEAM SEARCH
# ============================================================

def find_team(team_name):
    results = api_get(
        "/search",
        {"q": team_name},
    )

    if not isinstance(results, list):
        return None

    name_lower = team_name.lower().strip()

    # Exact match first
    for team in results:
        name = str(team.get("name", "")).strip()

        if name.lower() == name_lower:
            return team

    # Partial match second
    for team in results:
        name = str(team.get("name", "")).strip()

        if name_lower in name.lower():
            return team

    return None


# ============================================================
# MATCH DATA
# ============================================================

def get_team_matches(team_id):
    matches = api_get(
        "/matches",
        {
            "team": team_id,
            "season": SEASON,
        },
    )

    if not isinstance(matches, list):
        return []

    completed = []

    for match in matches:
        status = str(match.get("status", "")).lower()

        # OpenFoot may return different status values.
        # Exclude clearly upcoming/cancelled matches.
        excluded = [
            "scheduled",
            "upcoming",
            "cancelled",
            "canceled",
            "postponed",
        ]

        if any(x in status for x in excluded):
            continue

        home_team = match.get("homeTeam") or {}
        away_team = match.get("awayTeam") or {}

        home_score = extract_score(
            match.get("homeScore")
        )

        away_score = extract_score(
            match.get("awayScore")
        )

        # Some API versions may expose score fields differently.
        if home_score is None:
            home_score = extract_score(
                home_team.get("score")
            )

        if away_score is None:
            away_score = extract_score(
                away_team.get("score")
            )

        if home_score is None or away_score is None:
            continue

        match["_home_score"] = home_score
        match["_away_score"] = away_score

        completed.append(match)

    # Sort newest first when kickoffAt is available
    completed.sort(
        key=lambda x: str(x.get("kickoffAt", "")),
        reverse=True,
    )

    return completed


def extract_score(value):
    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, dict):
        for key in ["current", "goals", "score", "value"]:
            if key in value:
                result = extract_score(value[key])

                if result is not None:
                    return result

    if isinstance(value, str):
        try:
            return int(value)
        except Exception:
            return None

    return None


# ============================================================
# TEAM-PERSPECTIVE MATCH DATA
# ============================================================

def team_oriented_matches(matches, team_id):
    output = []

    for match in matches:
        home_team = match.get("homeTeam") or {}
        away_team = match.get("awayTeam") or {}

        home_id = str(home_team.get("id", ""))
        away_id = str(away_team.get("id", ""))

        hs = match.get("_home_score")
        aws = match.get("_away_score")

        if hs is None or aws is None:
            continue

        if home_id == str(team_id):
            output.append({
                "scored": hs,
                "conceded": aws,
                "venue": "home",
                "result": (
                    "W" if hs > aws
                    else "D" if hs == aws
                    else "L"
                ),
            })

        elif away_id == str(team_id):
            output.append({
                "scored": aws,
                "conceded": hs,
                "venue": "away",
                "result": (
                    "W" if aws > hs
                    else "D" if aws == hs
                    else "L"
                ),
            })

    return output


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
            "avg_gf": 0,
            "avg_ga": 0,
            "over15": 0,
            "over25": 0,
            "under35": 0,
            "btts": 0,
            "scoring": 0,
            "clean_sheet": 0,
        }

    games = len(matches)

    wins = sum(1 for m in matches if m["result"] == "W")
    draws = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")

    gf = sum(m["scored"] for m in matches)
    ga = sum(m["conceded"] for m in matches)

    over15 = sum(
        1 for m in matches
        if m["scored"] + m["conceded"] > 1
    )

    over25 = sum(
        1 for m in matches
        if m["scored"] + m["conceded"] > 2
    )

    under35 = sum(
        1 for m in matches
        if m["scored"] + m["conceded"] < 4
    )

    btts = sum(
        1 for m in matches
        if m["scored"] > 0 and m["conceded"] > 0
    )

    scoring = sum(
        1 for m in matches
        if m["scored"] > 0
    )

    clean_sheet = sum(
        1 for m in matches
        if m["conceded"] == 0
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
# VENUE STATS
# ============================================================

def get_venue_sample(matches, venue, limit=5):
    selected = [
        m for m in matches
        if m["venue"] == venue
    ]

    return selected[:limit]


# ============================================================
# EXPECTED GOALS
# ============================================================

def calculate_xg(
    home_stats,
    away_stats,
    home_venue,
    away_venue,
):
    """
    xG is built from:
    - team's recent attacking output
    - opponent defensive output
    - venue-specific attack/defense
    - conservative blending

    The purpose is to create a stable baseline rather than
    letting a tiny sample dominate the calculation.
    """

    # Overall attack / opponent defense
    home_base = (
        home_stats["avg_gf"] * 0.55
        + away_stats["avg_ga"] * 0.45
    )

    away_base = (
        away_stats["avg_gf"] * 0.55
        + home_stats["avg_ga"] * 0.45
    )

    # Venue evidence
    home_xg = home_base

    if home_venue["games"] > 0:
        home_venue_attack = home_venue["avg_gf"]
        away_venue_defense = away_venue["avg_ga"]

        venue_estimate = (
            home_venue_attack * 0.55
            + away_venue_defense * 0.45
        )

        venue_weight = min(
            0.40,
            0.15 * home_venue["games"]
        )

        home_xg = (
            home_base * (1 - venue_weight)
            + venue_estimate * venue_weight
        )

    away_xg = away_base

    if away_venue["games"] > 0:
        away_venue_attack = away_venue["avg_gf"]
        home_venue_defense = home_venue["avg_ga"]

        venue_estimate = (
            away_venue_attack * 0.55
            + home_venue_defense * 0.45
        )

        venue_weight = min(
            0.40,
            0.15 * away_venue["games"]
        )

        away_xg = (
            away_base * (1 - venue_weight)
            + venue_estimate * venue_weight
        )

    # Home advantage is modest rather than extreme.
    home_xg *= 1.04

    # Keep the model within reasonable boundaries.
    home_xg = max(0.20, min(3.50, home_xg))
    away_xg = max(0.20, min(3.50, away_xg))

    return home_xg, away_xg


# ============================================================
# POISSON ENGINE
# ============================================================

def poisson_probability(goals, lam):
    if lam <= 0:
        return 0.0

    return (
        math.exp(-lam)
        * (lam ** goals)
        / math.factorial(goals)
    )


def build_score_matrix(home_xg, away_xg, max_goals=10):
    matrix = []

    for home_goals in range(max_goals + 1):
        row = []

        home_prob = poisson_probability(
            home_goals,
            home_xg,
        )

        for away_goals in range(max_goals + 1):
            away_prob = poisson_probability(
                away_goals,
                away_xg,
            )

            row.append(home_prob * away_prob)

        matrix.append(row)

    return matrix


def poisson_markets(home_xg, away_xg):
    matrix = build_score_matrix(
        home_xg,
        away_xg,
        max_goals=10,
    )

    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    over15 = 0.0
    over25 = 0.0
    under35 = 0.0

    btts = 0.0
    home_score = 0.0
    away_score = 0.0

    for h in range(len(matrix)):
        for a in range(len(matrix[h])):
            probability = matrix[h][a]

            if h > a:
                home_win += probability
            elif h == a:
                draw += probability
            else:
                away_win += probability

            total = h + a

            if total >= 2:
                over15 += probability

            if total >= 3:
                over25 += probability

            if total <= 3:
                under35 += probability

            if h > 0 and a > 0:
                btts += probability

            if h > 0:
                home_score += probability

            if a > 0:
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
# HISTORICAL MARKET ESTIMATES
# ============================================================

def historical_market(home_stats, away_stats, home_venue, away_venue):
    """
    Produces historical evidence separately from the Poisson
    probabilities.
    """

    def average(values):
        values = [v for v in values if v is not None]

        if not values:
            return 50.0

        return mean(values)

    home_result_strength = (
        home_stats["wins"] / max(1, home_stats["games"]) * 100
    )

    away_unbeaten = (
        (
            away_stats["wins"]
            + away_stats["draws"]
        )
        / max(1, away_stats["games"])
        * 100
    )

    home_1x = average([
        (
            home_stats["wins"]
            + home_stats["draws"]
        )
        / max(1, home_stats["games"])
        * 100,
        (
            home_venue["wins"]
            + home_venue["draws"]
        )
        / max(1, home_venue["games"])
        * 100
        if home_venue["games"] > 0
        else None,
    ])

    away_x2 = average([
        (
            away_stats["wins"]
            + away_stats["draws"]
        )
        / max(1, away_stats["games"])
        * 100,
        (
            away_venue["wins"]
            + away_venue["draws"]
        )
        / max(1, away_venue["games"])
        * 100
        if away_venue["games"] > 0
        else None,
    ])

    over15 = average([
        home_stats["over15"],
        away_stats["over15"],
        home_venue["over15"]
        if home_venue["games"] > 0 else None,
        away_venue["over15"]
        if away_venue["games"] > 0 else None,
    ])

    over25 = average([
        home_stats["over25"],
        away_stats["over25"],
        home_venue["over25"]
        if home_venue["games"] > 0 else None,
        away_venue["over25"]
        if away_venue["games"] > 0 else None,
    ])

    under35 = average([
        home_stats["under35"],
        away_stats["under35"],
        home_venue["under35"]
        if home_venue["games"] > 0 else None,
        away_venue["under35"]
        if away_venue["games"] > 0 else None,
    ])

    btts = average([
        home_stats["btts"],
        away_stats["btts"],
        home_venue["btts"]
        if home_venue["games"] > 0 else None,
        away_venue["btts"]
        if away_venue["games"] > 0 else None,
    ])

    home_score = average([
        home_stats["scoring"],
        home_venue["scoring"]
        if home_venue["games"] > 0 else None,
    ])

    away_score = average([
        away_stats["scoring"],
        away_venue["scoring"]
        if away_venue["games"] > 0 else None,
    ])

    return {
        "1x": home_1x,
        "x2": away_x2,
        "over15": over15,
        "over25": over25,
        "under35": under35,
        "btts": btts,
        "home_score": home_score,
        "away_score": away_score,
        "home_win_strength": home_result_strength,
        "away_unbeaten_strength": away_unbeaten,
    }


# ============================================================
# SAMPLE-SIZE WEIGHT
# ============================================================

def sample_weight(home_n, away_n):
    n = min(home_n, away_n)

    if n <= 1:
        return 0.20

    if n == 2:
        return 0.30

    if n == 3:
        return 0.35

    if n == 4:
        return 0.40

    return 0.45


# ============================================================
# CALIBRATION
# ============================================================

def clamp(value, low=5.0, high=95.0):
    return max(low, min(high, value))


def shrink_probability(probability, strength=0.10):
    """
    Pulls probabilities modestly toward 50%.

    v2.7 deliberately uses less aggressive shrinkage than v2.6
    because Poisson now provides the mathematical baseline.
    """

    result = 50 + (
        probability - 50
    ) * (1 - strength)

    return clamp(result)


def blend_probability(
    poisson_value,
    historical_value,
    evidence_weight,
):
    """
    Poisson remains the primary mathematical source.
    Historical evidence modifies it rather than replacing it.
    """

    result = (
        poisson_value * (1 - evidence_weight)
        + historical_value * evidence_weight
    )

    return result


# ============================================================
# FINAL MARKET CALCULATION
# ============================================================

def calculate_markets(
    poisson,
    historical,
    home_n,
    away_n,
):
    evidence_weight = sample_weight(
        home_n,
        away_n,
    )

    markets = {}

    for key in [
        "over15",
        "over25",
        "under35",
        "btts",
        "home_score",
        "away_score",
    ]:
        raw = blend_probability(
            poisson[key],
            historical[key],
            evidence_weight,
        )

        markets[key] = shrink_probability(
            raw,
            strength=0.08,
        )

    # Result markets use Poisson 1X/X2 as the mathematical base.
    # Historical evidence has a smaller influence because tiny
    # samples can distort win/draw/loss percentages.
    result_weight = evidence_weight * 0.65

    markets["1x"] = shrink_probability(
        blend_probability(
            poisson["1x"],
            historical["1x"],
            result_weight,
        ),
        strength=0.08,
    )

    markets["x2"] = shrink_probability(
        blend_probability(
            poisson["x2"],
            historical["x2"],
            result_weight,
        ),
        strength=0.08,
    )

    return markets


# ============================================================
# EVIDENCE LABELS
# ============================================================

def evidence_label(probability):
    if probability >= 75:
        return "Strong"

    if probability >= 65:
        return "Moderate"

    return "Weak"


def market_icon(probability):
    if probability >= 75:
        return "🟢"

    if probability >= 60:
        return "🟡"

    return "🔴"


# ============================================================
# RESULT CONFLICT
# ============================================================

def has_result_conflict(markets):
    one_x = markets["1x"]
    x_two = markets["x2"]

    return (
        one_x >= 65
        and x_two >= 65
        and abs(one_x - x_two) <= 8
    )


# ============================================================
# TEAM ANALYSIS
# ============================================================

def collect_team_data(team):
    team_id = team.get("id")

    matches = get_team_matches(team_id)

    oriented = team_oriented_matches(
        matches,
        team_id,
    )

    recent = oriented[:SAMPLE_SIZE]

    return {
        "team": team,
        "all": oriented,
        "recent": recent,
        "stats": calculate_stats(recent),
        "home": calculate_stats(
            get_venue_sample(
                oriented,
                "home",
                SAMPLE_SIZE,
            )
        ),
        "away": calculate_stats(
            get_venue_sample(
                oriented,
                "away",
                SAMPLE_SIZE,
            )
        ),
    }


# ============================================================
# FORMAT HELPERS
# ============================================================

def form_string(matches):
    return "".join(
        m["result"]
        for m in matches
    )


def format_team_line(name, data):
    s = data["stats"]

    return (
        f"{name} last 5: "
        f"{form_string(data['recent'])}, "
        f"W/D/L {s['wins']}/{s['draws']}/{s['losses']}, "
        f"GF{s['gf']} GA{s['ga']}, "
        f"avg {s['avg_gf']:.2f}/{s['avg_ga']:.2f}, "
        f"O1.5 {s['over15']:.0f}%, "
        f"O2.5 {s['over25']:.0f}%, "
        f"U3.5 {s['under35']:.0f}%, "
        f"BTTS {s['btts']:.0f}%, "
        f"scoring {s['scoring']:.0f}%, "
        f"CS {s['clean_sheet']:.0f}%"
    )


def format_venue_line(
    name,
    venue_name,
    stats,
):
    return (
        f"{name} {venue_name} sample {stats['games']}: "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']}, "
        f"scoring {stats['scoring']:.0f}%, "
        f"CS {stats['clean_sheet']:.0f}%, "
        f"O2.5 {stats['over25']:.0f}%, "
        f"BTTS {stats['btts']:.0f}%"
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        f"⚽ Welcome to GoalLogic AI v{VERSION}!\n\n"
        "I analyze football matches using historical statistics, "
        "venue evidence, expected goals and a Poisson probability model.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )

    await update.message.reply_text(message)


# ============================================================
# /APITEST
# ============================================================

async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        team = find_team("Chelsea")

        if not team:
            await update.message.reply_text(
                "❌ OpenFoot API connected, but Chelsea was not found."
            )
            return

        team_id = team.get("id")

        await update.message.reply_text(
            "🔧 FOOTBALL API TEST\n\n"
            "OpenFoot API connection works.\n"
            f"Found Chelsea.\n"
            f"Team ID: {team_id}"
        )

    except Exception as e:
        await update.message.reply_text(
            "❌ FOOTBALL API TEST FAILED\n\n"
            f"{str(e)}"
        )


# ============================================================
# /TEAM
# ============================================================

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

    try:
        results = api_get(
            "/search",
            {"q": query},
        )

        if not isinstance(results, list) or not results:
            await update.message.reply_text(
                f"❌ No teams found for: {query}"
            )
            return

        lines = [
            f"🔎 Teams found for: {query}\n"
        ]

        for team in results[:10]:
            name = team.get("name", "Unknown")
            team_id = team.get("id", "Unknown")
            country = team.get("country", "")

            country_text = (
                f" ({country})"
                if country
                else ""
            )

            lines.append(
                f"• {name}{country_text}\n"
                f"  ID: {team_id}"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Error:\n{str(e)}"
        )


# ============================================================
# /FIXTURES
# ============================================================

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

    try:
        team = find_team(query)

        if not team:
            await update.message.reply_text(
                f"❌ Team not found: {query}"
            )
            return

        matches = get_team_matches(
            team.get("id")
        )

        if not matches:
            await update.message.reply_text(
                f"❌ No completed {SEASON} matches found for {query}."
            )
            return

        lines = [
            f"📅 {team.get('name', query)} fixtures",
            f"Season: {SEASON}\n",
        ]

        for match in matches[:10]:
            home = (
                match.get("homeTeam") or {}
            ).get("name", "Home")

            away = (
                match.get("awayTeam") or {}
            ).get("name", "Away")

            kickoff = match.get(
                "kickoffAt",
                "Unknown date",
            )

            hs = match.get("_home_score")
            aws = match.get("_away_score")

            lines.append(
                f"🏟️ {home} {hs}-{aws} {away}\n"
                f"📅 {kickoff}"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Error:\n{str(e)}"
        )


# ============================================================
# /ANALYZE
# ============================================================

async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/analyze Chelsea vs Arsenal"
        )
        return

    query = " ".join(context.args)

    if " vs " not in query.lower():
        await update.message.reply_text(
            "Please use:\n/analyze Chelsea vs Arsenal"
        )
        return

    parts = query.split(
        " vs ",
        1,
    )

    if len(parts) != 2:
        # Try case-insensitive split
        lower_query = query.lower()
        index = lower_query.find(" vs ")

        if index == -1:
            await update.message.reply_text(
                "Please use:\n/analyze Chelsea vs Arsenal"
            )
            return

        home_name = query[:index].strip()
        away_name = query[index + 4:].strip()

    else:
        home_name = parts[0].strip()
        away_name = parts[1].strip()

    try:
        home_team = find_team(home_name)
        away_team = find_team(away_name)

        if not home_team:
            await update.message.reply_text(
                f"❌ Could not find home team: {home_name}"
            )
            return

        if not away_team:
            await update.message.reply_text(
                f"❌ Could not find away team: {away_name}"
            )
            return

        home_data = collect_team_data(
            home_team
        )

        away_data = collect_team_data(
            away_team
        )

        if len(home_data["recent"]) == 0:
            await update.message.reply_text(
                f"❌ No recent {SEASON} data found for "
                f"{home_team.get('name')}."
            )
            return

        if len(away_data["recent"]) == 0:
            await update.message.reply_text(
                f"❌ No recent {SEASON} data found for "
                f"{away_team.get('name')}."
            )
            return

        home_name_final = home_team.get(
            "name",
            home_name,
        )

        away_name_final = away_team.get(
            "name",
            away_name,
        )

        # Venue samples
        home_venue = home_data["home"]
        away_venue = away_data["away"]

        # xG
        home_xg, away_xg = calculate_xg(
            home_data["stats"],
            away_data["stats"],
            home_venue,
            away_venue,
        )

        total_xg = home_xg + away_xg

        # Mathematical Poisson probabilities
        poisson = poisson_markets(
            home_xg,
            away_xg,
        )

        # Historical evidence
        historical = historical_market(
            home_data["stats"],
            away_data["stats"],
            home_venue,
            away_venue,
        )

        # Final calibrated markets
        markets = calculate_markets(
            poisson,
            historical,
            home_data["stats"]["games"],
            away_data["stats"]["games"],
        )

        # Result conflict
        conflict = has_result_conflict(
            markets
        )

        # Signal names
        signal_names = {
            "1x": "1X",
            "x2": "X2",
            "over15": "O1.5",
            "over25": "O2.5",
            "under35": "U3.5",
            "btts": "BTTS",
            "home_score": "Team1 score",
            "away_score": "Team2 score",
        }

        # Sort strictly by confidence
        sorted_signals = sorted(
            markets.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        # If 1X and X2 conflict, don't let both dominate
        if conflict:
            sorted_signals = [
                item
                for item in sorted_signals
                if item[0] not in ("1x", "x2")
            ] + [
                item
                for item in sorted_signals
                if item[0] in ("1x", "x2")
            ]

            sorted_signals.sort(
                key=lambda item: item[1],
                reverse=True,
            )

        primary_key, primary_probability = (
            sorted_signals[0]
        )

        # Header
        lines = [
            f"⚽ GOALLOGIC AI v{VERSION}",
            "",
            f"🏟️ {home_name_final} vs {away_name_final}",
            "",
            f"Season: {SEASON}",
            f"Sample: Last {SAMPLE_SIZE} available matches",
            "",
            format_team_line(
                home_name_final,
                home_data,
            ),
            "",
            format_team_line(
                away_name_final,
                away_data,
            ),
            "",
            format_venue_line(
                home_name_final,
                "HOME",
                home_venue,
            ),
            "",
            format_venue_line(
                away_name_final,
                "AWAY",
                away_venue,
            ),
            "",
            "🎯 EXPECTED GOALS",
            f"{home_name_final}: {home_xg:.2f}",
            f"{away_name_final}: {away_xg:.2f}",
            f"Total expected goals: {total_xg:.2f}",
            "",
            "📊 MARKETS",
        ]

        market_order = [
            "1x",
            "x2",
            "over15",
            "over25",
            "under35",
            "btts",
            "home_score",
            "away_score",
        ]

        for key in market_order:
            probability = markets[key]

            icon = market_icon(
                probability
            )

            label = evidence_label(
                probability
            )

            lines.append(
                f"{icon} {signal_names[key]}: "
                f"{probability:.0f}% — {label} evidence"
            )

        lines.extend([
            "",
            "🏆 TOP SIGNALS",
        ])

        for index, (key, probability) in enumerate(
            sorted_signals[:3],
            start=1,
        ):
            lines.append(
                f"{index}. {signal_names[key]} — "
                f"{probability:.0f}% "
                f"({evidence_label(probability)})"
            )

        lines.extend([
            "",
            "🎯 PRIMARY SIGNAL",
            f"{signal_names[primary_key]} — "
            f"{primary_probability:.0f}%",
        ])

        if conflict:
            lines.extend([
                "",
                "⚠️ RESULT MARKET CONFLICT",
                "1X and X2 are both relatively elevated. "
                "The model does not treat either result market "
                "as a decisive signal.",
            ])

        lines.extend([
            "",
            "📌 Advice: BET only if the available odds "
            "justify the risk.",
            "",
            "⚠️ Statistical signal only — not a guarantee.",
            "",
            f"🤖 v{VERSION} Model note: "
            "Probabilities use a Poisson scoreline model "
            "from expected goals, blended with historical "
            "and venue evidence. Sample-size calibration "
            "reduces overconfidence when evidence is limited.",
        ])

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:
        await update.message.reply_text(
            "❌ ANALYSIS ERROR\n\n"
            f"{str(e)}"
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

    # Start Render health server
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
        f"GoalLogic AI v{VERSION} Telegram bot is starting..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
