import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from statistics import mean

import requests

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY")

OPENFOOT_BASE = "https://openfootapi.com"

CURRENT_SEASON = "2026/27"
RECENT_MATCHES = 5

MIN_CONFIDENCE = 50
MAX_CONFIDENCE = 85


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GoalLogic AI v2.1 is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"GoalLogic AI v2.1 is running on port {port}.")
    server.serve_forever()


# ============================================================
# OPENFOOT API
# ============================================================

def openfoot_get(endpoint, params=None):

    if not OPENFOOT_API_KEY:
        raise RuntimeError("OPENFOOT_API_KEY is missing.")

    url = f"{OPENFOOT_BASE}{endpoint}"

    headers = {
        "Authorization": f"Bearer {OPENFOOT_API_KEY}"
    }

    response = requests.get(
        url,
        headers=headers,
        params=params or {},
        timeout=20
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# GENERAL HELPERS
# ============================================================

def normalize_name(value):

    value = str(value or "").lower().strip()

    value = re.sub(
        r"[^a-z0-9]+",
        "",
        value
    )

    return value


def safe_percentage(value):

    try:
        return round(float(value))
    except Exception:
        return 0


def get_score(match):

    score = match.get("score")

    if not isinstance(score, dict):
        return None

    fulltime = score.get("fulltime")

    if not isinstance(fulltime, dict):
        return None

    home = fulltime.get("home")
    away = fulltime.get("away")

    try:
        if home is None or away is None:
            return None

        return int(home), int(away)

    except Exception:
        return None


def team_side(match, team_id):

    teams = match.get("teams") or {}

    home = teams.get("home") or {}
    away = teams.get("away") or {}

    if str(home.get("id")) == str(team_id):
        return "home"

    if str(away.get("id")) == str(team_id):
        return "away"

    return None


# ============================================================
# TEAM SEARCH
# ============================================================

def search_team(team_name):

    data = openfoot_get(
        "/v1/search",
        {
            "q": team_name
        }
    )

    results = data.get("data", [])

    if not results:
        return None

    target = normalize_name(team_name)

    # Exact match
    for item in results:

        name = (
            item.get("name")
            or item.get("team_name")
            or item.get("display_name")
            or ""
        )

        if normalize_name(name) == target:
            return item

    # Partial match
    for item in results:

        name = (
            item.get("name")
            or item.get("team_name")
            or item.get("display_name")
            or ""
        )

        normalized = normalize_name(name)

        if target in normalized or normalized in target:
            return item

    return results[0]


# ============================================================
# RECENT MATCHES
# ============================================================

def get_recent_matches(team):

    team_id = team.get("id")

    if not team_id:
        return []

    data = openfoot_get(
        "/v1/matches",
        {
            "team": team_id,
            "season": CURRENT_SEASON,
            "status": "finished"
        }
    )

    matches = data.get("data", [])

    parsed = []

    for match in matches:

        score = get_score(match)

        if not score:
            continue

        side = team_side(match, team_id)

        if side not in ("home", "away"):
            continue

        home_score, away_score = score

        if side == "home":
            goals_for = home_score
            goals_against = away_score
        else:
            goals_for = away_score
            goals_against = home_score

        if goals_for > goals_against:
            result = "W"
        elif goals_for == goals_against:
            result = "D"
        else:
            result = "L"

        date_value = (
            match.get("date")
            or match.get("fixture_date")
            or ""
        )

        parsed.append({
            "date": date_value,
            "side": side,
            "goals_for": goals_for,
            "goals_against": goals_against,
            "result": result,
            "total_goals": goals_for + goals_against,
        })

    parsed.sort(
        key=lambda x: x.get("date", ""),
        reverse=True
    )

    return parsed[:RECENT_MATCHES]


# ============================================================
# BASIC STATISTICS
# ============================================================

def calculate_stats(matches):

    sample = len(matches)

    if sample == 0:
        return {
            "sample": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "win_rate": 0,
            "draw_rate": 0,
            "loss_rate": 0,
            "gf": 0,
            "ga": 0,
            "avg_scored": 0,
            "avg_conceded": 0,
            "avg_total": 0,
            "over05": 0,
            "over15": 0,
            "over25": 0,
            "under35": 0,
            "btts": 0,
            "scoring": 0,
            "clean_sheet": 0,
        }

    wins = sum(1 for m in matches if m["result"] == "W")
    draws = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")

    gf = sum(m["goals_for"] for m in matches)
    ga = sum(m["goals_against"] for m in matches)

    over05 = sum(
        1 for m in matches
        if m["total_goals"] > 0
    )

    over15 = sum(
        1 for m in matches
        if m["total_goals"] > 1
    )

    over25 = sum(
        1 for m in matches
        if m["total_goals"] > 2
    )

    under35 = sum(
        1 for m in matches
        if m["total_goals"] < 4
    )

    btts = sum(
        1 for m in matches
        if m["goals_for"] > 0
        and m["goals_against"] > 0
    )

    scoring = sum(
        1 for m in matches
        if m["goals_for"] > 0
    )

    clean_sheet = sum(
        1 for m in matches
        if m["goals_against"] == 0
    )

    return {
        "sample": sample,

        "wins": wins,
        "draws": draws,
        "losses": losses,

        "win_rate": safe_percentage(wins / sample * 100),
        "draw_rate": safe_percentage(draws / sample * 100),
        "loss_rate": safe_percentage(losses / sample * 100),

        "gf": gf,
        "ga": ga,

        "avg_scored": round(gf / sample, 2),
        "avg_conceded": round(ga / sample, 2),
        "avg_total": round(
            (gf + ga) / sample,
            2
        ),

        "over05": safe_percentage(
            over05 / sample * 100
        ),

        "over15": safe_percentage(
            over15 / sample * 100
        ),

        "over25": safe_percentage(
            over25 / sample * 100
        ),

        "under35": safe_percentage(
            under35 / sample * 100
        ),

        "btts": safe_percentage(
            btts / sample * 100
        ),

        "scoring": safe_percentage(
            scoring / sample * 100
        ),

        "clean_sheet": safe_percentage(
            clean_sheet / sample * 100
        ),
    }


# ============================================================
# HOME / AWAY STATISTICS
# ============================================================

def venue_stats(matches, venue):

    filtered = [
        m for m in matches
        if m["side"] == venue
    ]

    stats = calculate_stats(filtered)

    if stats["sample"] == 0:
        stats["sample"] = 0

    return stats


# ============================================================
# SAMPLE RELIABILITY
# ============================================================

def sample_weight(sample):

    if sample <= 0:
        return 0.20

    if sample == 1:
        return 0.30

    if sample == 2:
        return 0.45

    if sample == 3:
        return 0.60

    if sample == 4:
        return 0.80

    return 1.00


# ============================================================
# CONFIDENCE ENGINE
# ============================================================

def confidence(
    base,
    supports=0,
    contradictions=0,
    sample=5,
    venue_sample=0,
    strong_venue=False
):

    reliability = sample_weight(sample)

    # Small samples are deliberately compressed.
    adjusted = 50 + (
        (base - 50)
        * 0.45
        * reliability
    )

    # Positive evidence.
    adjusted += supports * 1.5

    # Contradictory evidence.
    adjusted -= contradictions * 3.0

    # Venue evidence needs its own reliability penalty.
    if venue_sample == 1:
        adjusted -= 5

    elif venue_sample == 2:
        adjusted -= 3

    elif venue_sample == 3:
        adjusted -= 1

    # Strong venue evidence can add a small boost,
    # but never enough to overpower the sample penalty.
    if strong_venue and venue_sample >= 3:
        adjusted += 2

    # Hard ceiling for small samples.
    if sample <= 2:
        adjusted = min(adjusted, 62)

    elif sample == 3:
        adjusted = min(adjusted, 67)

    elif sample == 4:
        adjusted = min(adjusted, 73)

    return round(
        max(
            MIN_CONFIDENCE,
            min(MAX_CONFIDENCE, adjusted)
        )
    )


def grade(value):

    if value >= 78:
        return "🟢 STRONG"

    if value >= 68:
        return "🟢 GOOD"

    if value >= 58:
        return "🟡 MODERATE"

    return "🔴 WEAK"


def advice(value):

    if value >= 68:
        return "BET"

    if value >= 58:
        return "CAUTION"

    return "AVOID"


# ============================================================
# MARKET ENGINE
# ============================================================

def market_engine(team1_stats, team2_stats,
                  team1_home, team2_away):

    markets = {}

    # --------------------------------------------------------
    # 1X
    # --------------------------------------------------------

    base = mean([
        team1_stats["win_rate"] + team1_stats["draw_rate"],
        team2_stats["loss_rate"],
        team1_home["win_rate"] + team1_home["draw_rate"],
    ])

    supports = 0
    contradictions = 0

    if team1_stats["wins"] + team1_stats["draws"] >= 3:
        supports += 1

    if team2_stats["losses"] >= 3:
        supports += 1

    if team1_home["wins"] + team1_home["draws"] >= 2:
        supports += 1

    if team2_away["wins"] + team2_away["draws"] >= 2:
        supports += 1

    if team1_home["sample"] >= 2 and (
        team1_home["wins"] + team1_home["draws"]
    ) == 0:
        contradictions += 2

    markets["1X"] = confidence(
        base,
        supports,
        contradictions,
        team1_stats["sample"],
        team1_home["sample"]
    )

    # --------------------------------------------------------
    # X2
    # --------------------------------------------------------

    base = mean([
        team2_stats["win_rate"] + team2_stats["draw_rate"],
        team1_stats["loss_rate"],
        team2_away["win_rate"] + team2_away["draw_rate"],
    ])

    supports = 0
    contradictions = 0

    if team2_stats["wins"] + team2_stats["draws"] >= 3:
        supports += 1

    if team1_stats["losses"] >= 3:
        supports += 1

    if team2_away["wins"] + team2_away["draws"] >= 2:
        supports += 1

    if team1_home["losses"] >= 1:
        supports += 1

    if team2_away["sample"] >= 2 and (
        team2_away["wins"] + team2_away["draws"]
    ) == 0:
        contradictions += 2

    markets["X2"] = confidence(
        base,
        supports,
        contradictions,
        team2_stats["sample"],
        team2_away["sample"]
    )

    # --------------------------------------------------------
    # 12 - NO DRAW
    # --------------------------------------------------------

    base = mean([
        team1_stats["win_rate"],
        team2_stats["win_rate"],
        team1_home["win_rate"],
        team2_away["win_rate"],
    ])

    supports = 0
    contradictions = 0

    if team1_stats["draw_rate"] <= 20:
        supports += 1

    if team2_stats["draw_rate"] <= 20:
        supports += 1

    if team1_home["draw_rate"] <= 20:
        supports += 1

    if team2_away["draw_rate"] <= 20:
        supports += 1

    if team1_home["draw_rate"] >= 50:
        contradictions += 2

    if team2_away["draw_rate"] >= 50:
        contradictions += 2

    markets["12"] = confidence(
        base,
        supports,
        contradictions,
        min(
            team1_stats["sample"],
            team2_stats["sample"]
        ),
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # OVER 0.5
    # --------------------------------------------------------

    base = mean([
        team1_stats["over05"],
        team2_stats["over05"],
    ])

    supports = 0
    contradictions = 0

    if team1_stats["over05"] >= 80:
        supports += 1

    if team2_stats["over05"] >= 80:
        supports += 1

    if team1_home["sample"] >= 2:
        if team1_home["over05"] >= 80:
            supports += 1

    if team2_away["sample"] >= 2:
        if team2_away["over05"] >= 80:
            supports += 1

    # Do not let 100% over 0.5 from five matches become
    # an automatic high-confidence bet.
    if (
        team1_home["over05"] < 100
        and team2_away["over05"] < 100
    ):
        contradictions += 1

    markets["Over 0.5"] = confidence(
        base,
        supports,
        contradictions,
        min(
            team1_stats["sample"],
            team2_stats["sample"]
        ),
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # OVER 1.5
    # --------------------------------------------------------

    base = mean([
        team1_stats["over15"],
        team2_stats["over15"],
        team1_home["over15"],
        team2_away["over15"],
    ])

    supports = 0
    contradictions = 0

    if team1_stats["over15"] >= 70:
        supports += 1

    if team2_stats["over15"] >= 70:
        supports += 1

    if team1_home["over15"] >= 70:
        supports += 1

    if team2_away["over15"] >= 70:
        supports += 1

    if team1_home["over15"] <= 50:
        contradictions += 1

    if team2_away["over15"] <= 50:
        contradictions += 1

    markets["Over 1.5"] = confidence(
        base,
        supports,
        contradictions,
        min(
            team1_stats["sample"],
            team2_stats["sample"]
        ),
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # OVER 2.5
    # --------------------------------------------------------

    base = mean([
        team1_stats["over25"],
        team2_stats["over25"],
        team1_home["over25"],
        team2_away["over25"],
    ])

    supports = 0
    contradictions = 0

    if team1_stats["over25"] >= 60:
        supports += 1

    if team2_stats["over25"] >= 60:
        supports += 1

    if team1_home["over25"] >= 60:
        supports += 1

    if team2_away["over25"] >= 60:
        supports += 1

    if team1_home["over25"] <= 33:
        contradictions += 2

    if team2_away["over25"] <= 33:
        contradictions += 2

    markets["Over 2.5"] = confidence(
        base,
        supports,
        contradictions,
        min(
            team1_stats["sample"],
            team2_stats["sample"]
        ),
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # UNDER 3.5
    # --------------------------------------------------------

    base = mean([
        team1_stats["under35"],
        team2_stats["under35"],
        team1_home["under35"],
        team2_away["under35"],
    ])

    supports = 0
    contradictions = 0

    if team1_stats["under35"] >= 70:
        supports += 1

    if team2_stats["under35"] >= 70:
        supports += 1

    if team1_home["under35"] >= 70:
        supports += 1

    if team2_away["under35"] >= 70:
        supports += 1

    if team1_home["under35"] <= 50:
        contradictions += 1

    if team2_away["under35"] <= 50:
        contradictions += 1

    markets["Under 3.5"] = confidence(
        base,
        supports,
        contradictions,
        min(
            team1_stats["sample"],
            team2_stats["sample"]
        ),
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # BTTS YES
    # --------------------------------------------------------

    base = mean([
        team1_stats["btts"],
        team2_stats["btts"],
        team1_home["btts"],
        team2_away["btts"],
    ])

    supports = 0
    contradictions = 0

    if team1_stats["btts"] >= 60:
        supports += 1

    if team2_stats["btts"] >= 60:
        supports += 1

    if team1_home["btts"] >= 60:
        supports += 1

    if team2_away["btts"] >= 60:
        supports += 1

    if team1_home["btts"] == 0:
        contradictions += 2

    if team2_away["btts"] == 0:
        contradictions += 2

    if team1_stats["clean_sheet"] >= 60:
        contradictions += 1

    if team2_stats["clean_sheet"] >= 60:
        contradictions += 1

    markets["BTTS Yes"] = confidence(
        base,
        supports,
        contradictions,
        min(
            team1_stats["sample"],
            team2_stats["sample"]
        ),
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # TEAM 1 TO SCORE
    # --------------------------------------------------------

    base = mean([
        team1_stats["scoring"],
        100 - team2_stats["clean_sheet"],
        team1_home["scoring"],
        100 - team2_away["clean_sheet"],
    ])

    supports = 0
    contradictions = 0

    if team1_stats["scoring"] >= 70:
        supports += 1

    if team1_home["scoring"] >= 70:
        supports += 1

    if team2_stats["clean_sheet"] <= 40:
        supports += 1

    if team2_away["clean_sheet"] <= 40:
        supports += 1

    if team1_home["scoring"] <= 50:
        contradictions += 2

    if team2_away["clean_sheet"] >= 60:
        contradictions += 2

    if team1_stats["scoring"] <= 40:
        contradictions += 2

    markets[f"{team1_stats['name']} to score"] = confidence(
        base,
        supports,
        contradictions,
        team1_stats["sample"],
        team1_home["sample"]
    )

    # --------------------------------------------------------
    # TEAM 2 TO SCORE
    # --------------------------------------------------------

    base = mean([
        team2_stats["scoring"],
        100 - team1_stats["clean_sheet"],
        team2_away["scoring"],
        100 - team1_home["clean_sheet"],
    ])

    supports = 0
    contradictions = 0

    if team2_stats["scoring"] >= 70:
        supports += 1

    if team2_away["scoring"] >= 70:
        supports += 1

    if team1_stats["clean_sheet"] <= 40:
        supports += 1

    if team1_home["clean_sheet"] <= 40:
        supports += 1

    if team2_away["scoring"] <= 50:
        contradictions += 2

    if team1_home["clean_sheet"] >= 60:
        contradictions += 2

    if team2_stats["scoring"] <= 40:
        contradictions += 2

    markets[f"{team2_stats['name']} to score"] = confidence(
        base,
        supports,
        contradictions,
        team2_stats["sample"],
        team2_away["sample"]
    )

    return markets


# ============================================================
# PRIMARY SIGNAL
# ============================================================

def choose_primary(markets):

    # Only consider markets that have reached BET level.
    candidates = [
        (name, value)
        for name, value in markets.items()
        if value >= 68
    ]

    if not candidates:
        return None

    # Broad markets are deliberately given a small penalty
    # when selecting the primary signal.
    priority_penalty = {
        "Over 0.5": 5,
        "12": 4,
        "Over 1.5": 2,
        "Under 3.5": 0,
        "Over 2.5": 0,
        "BTTS Yes": 1,
    }

    scored = []

    for name, value in candidates:

        penalty = priority_penalty.get(name, 0)

        selection_score = value - penalty

        scored.append(
            (
                selection_score,
                value,
                name
            )
        )

    scored.sort(
        key=lambda x: (x[0], x[1]),
        reverse=True
    )

    _, value, name = scored[0]

    return {
        "name": name,
        "confidence": value,
        "grade": grade(value),
        "advice": advice(value),
    }


# ============================================================
# ANALYSIS
# ============================================================

def analyze_match(team1_name, team2_name):

    team1 = search_team(team1_name)
    team2 = search_team(team2_name)

    if not team1:
        return f"❌ Could not find {team1_name}."

    if not team2:
        return f"❌ Could not find {team2_name}."

    team1_matches = get_recent_matches(team1)
    team2_matches = get_recent_matches(team2)

    if len(team1_matches) < 3:
        return (
            f"❌ Not enough recent data for {team1_name}.\n"
            f"Available matches: {len(team1_matches)}"
        )

    if len(team2_matches) < 3:
        return (
            f"❌ Not enough recent data for {team2_name}.\n"
            f"Available matches: {len(team2_matches)}"
        )

    team1_stats = calculate_stats(team1_matches)
    team2_stats = calculate_stats(team2_matches)

    team1_stats["name"] = team1_name
    team2_stats["name"] = team2_name

    team1_home = venue_stats(
        team1_matches,
        "home"
    )

    team2_away = venue_stats(
        team2_matches,
        "away"
    )

    markets = market_engine(
        team1_stats,
        team2_stats,
        team1_home,
        team2_away
    )

    primary = choose_primary(markets)

    team1_form = "".join(
        m["result"]
        for m in team1_matches
    )

    team2_form = "".join(
        m["result"]
        for m in team2_matches
    )

    lines = []

    lines.append(
        "⚽ GOALLOGIC AI v2.1 — MATCH ANALYSIS"
    )

    lines.append("")
    lines.append(
        f"🏟️ {team1_name} vs {team2_name}"
    )

    lines.append(
        f"Season: {CURRENT_SEASON}"
    )

    lines.append(
        f"Sample: Last {RECENT_MATCHES} available matches"
    )

    lines.append("")

    # --------------------------------------------------------
    # TEAM 1
    # --------------------------------------------------------

    lines.append(
        f"📊 {team1_name.upper()} — LAST {team1_stats['sample']}"
    )

    lines.append(
        f"Form: {team1_form}"
    )

    lines.append(
        f"W/D/L: "
        f"{team1_stats['wins']}/"
        f"{team1_stats['draws']}/"
        f"{team1_stats['losses']}"
    )

    lines.append(
        f"Goals scored: {team1_stats['gf']}"
    )

    lines.append(
        f"Goals conceded: {team1_stats['ga']}"
    )

    lines.append(
        f"Avg scored: {team1_stats['avg_scored']:.2f}"
    )

    lines.append(
        f"Avg conceded: {team1_stats['avg_conceded']:.2f}"
    )

    lines.append(
        f"Avg total goals: {team1_stats['avg_total']:.2f}"
    )

    lines.append(
        f"Over 0.5: {team1_stats['over05']}%"
    )

    lines.append(
        f"Over 1.5: {team1_stats['over15']}%"
    )

    lines.append(
        f"Over 2.5: {team1_stats['over25']}%"
    )

    lines.append(
        f"Under 3.5: {team1_stats['under35']}%"
    )

    lines.append(
        f"BTTS: {team1_stats['btts']}%"
    )

    lines.append(
        f"Scoring consistency: {team1_stats['scoring']}%"
    )

    lines.append(
        f"Clean sheets: {team1_stats['clean_sheet']}%"
    )

    lines.append("")

    # --------------------------------------------------------
    # TEAM 2
    # --------------------------------------------------------

    lines.append(
        f"📊 {team2_name.upper()} — LAST {team2_stats['sample']}"
    )

    lines.append(
        f"Form: {team2_form}"
    )

    lines.append(
        f"W/D/L: "
        f"{team2_stats['wins']}/"
        f"{team2_stats['draws']}/"
        f"{team2_stats['losses']}"
    )

    lines.append(
        f"Goals scored: {team2_stats['gf']}"
    )

    lines.append(
        f"Goals conceded: {team2_stats['ga']}"
    )

    lines.append(
        f"Avg scored: {team2_stats['avg_scored']:.2f}"
    )

    lines.append(
        f"Avg conceded: {team2_stats['avg_conceded']:.2f}"
    )

    lines.append(
        f"Avg total goals: {team2_stats['avg_total']:.2f}"
    )

    lines.append(
        f"Over 0.5: {team2_stats['over05']}%"
    )

    lines.append(
        f"Over 1.5: {team2_stats['over15']}%"
    )

    lines.append(
        f"Over 2.5: {team2_stats['over25']}%"
    )

    lines.append(
        f"Under 3.5: {team2_stats['under35']}%"
    )

    lines.append(
        f"BTTS: {team2_stats['btts']}%"
    )

    lines.append(
        f"Scoring consistency: {team2_stats['scoring']}%"
    )

    lines.append(
        f"Clean sheets: {team2_stats['clean_sheet']}%"
    )

    lines.append("")

    # --------------------------------------------------------
    # VENUE
    # --------------------------------------------------------

    lines.append(
        "🏠 HOME / ✈️ AWAY CONTEXT"
    )

    lines.append("")

    lines.append(
        f"{team1_name} HOME — "
        f"Sample {team1_home['sample']}"
    )

    lines.append(
        f"W/D/L: "
        f"{team1_home['wins']}/"
        f"{team1_home['draws']}/"
        f"{team1_home['losses']}"
    )

    lines.append(
        f"Scoring: {team1_home['scoring']}%"
    )

    lines.append(
        f"Clean sheets: {team1_home['clean_sheet']}%"
    )

    lines.append(
        f"Over 2.5: {team1_home['over25']}%"
    )

    lines.append(
        f"BTTS: {team1_home['btts']}%"
    )

    lines.append("")

    lines.append(
        f"{team2_name} AWAY — "
        f"Sample {team2_away['sample']}"
    )

    lines.append(
        f"W/D/L: "
        f"{team2_away['wins']}/"
        f"{team2_away['draws']}/"
        f"{team2_away['losses']}"
    )

    lines.append(
        f"Scoring: {team2_away['scoring']}%"
    )

    lines.append(
        f"Clean sheets: {team2_away['clean_sheet']}%"
    )

    lines.append(
        f"Over 2.5: {team2_away['over25']}%"
    )

    lines.append(
        f"BTTS: {team2_away['btts']}%"
    )

    lines.append("")

    # --------------------------------------------------------
    # MARKETS
    # --------------------------------------------------------

    lines.append(
        "📈 MARKET CONFIDENCE"
    )

    lines.append("")

    for name, value in markets.items():

        lines.append(
            f"{name}: {value}% — "
            f"{grade(value)} — "
            f"{advice(value)}"
        )

    lines.append("")

    # --------------------------------------------------------
    # PRIMARY
    # --------------------------------------------------------

    lines.append(
        "🎯 PRIMARY SIGNAL"
    )

    if primary:

        lines.append(
            f"{primary['name']}"
        )

        lines.append(
            f"Confidence: {primary['confidence']}%"
        )

        lines.append(
            f"Grade: {primary['grade']}"
        )

        lines.append(
            f"Advice: {primary['advice']}"
        )

    else:

        lines.append(
            "No market reached the 68% confidence threshold."
        )

        lines.append(
            "Advice: AVOID / WAIT"
        )

    lines.append("")

    lines.append(
        "🧠 ENGINE NOTE"
    )

    lines.append(
        "Confidence combines recent form, attack, "
        "defence, goal environment, home/away evidence "
        "and sample-size reliability."
    )

    lines.append(
        "Broad markets receive additional caution when "
        "their evidence comes mainly from small samples."
    )

    lines.append("")

    lines.append(
        "⚠️ Confidence figures are statistical indicators, "
        "not guarantees of the match result."
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        "⚽ Welcome to GoalLogic AI v2.1!\n\n"
        "I analyze football matches using recent "
        "historical statistics.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )

    await update.message.reply_text(message)


async def team_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "Usage:\n/team Chelsea"
        )

        return

    team_name = " ".join(context.args)

    try:

        team = search_team(team_name)

        if not team:

            await update.message.reply_text(
                f"❌ Could not find {team_name}."
            )

            return

        name = (
            team.get("name")
            or team.get("team_name")
            or team_name
        )

        team_id = team.get("id")
        country = team.get("country") or "Unknown"

        message = (
            f"⚽ TEAM SEARCH\n\n"
            f"Team: {name}\n"
            f"ID: {team_id}\n"
            f"Country: {country}"
        )

        await update.message.reply_text(message)

    except Exception as error:

        await update.message.reply_text(
            f"❌ Team search error:\n{error}"
        )


async def fixtures_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "Usage:\n/fixtures Chelsea"
        )

        return

    team_name = " ".join(context.args)

    try:

        team = search_team(team_name)

        if not team:

            await update.message.reply_text(
                f"❌ Could not find {team_name}."
            )

            return

        team_id = team.get("id")

        data = openfoot_get(
            "/v1/matches",
            {
                "team": team_id,
                "season": CURRENT_SEASON
            }
        )

        matches = data.get("data", [])

        if not matches:

            await update.message.reply_text(
                f"❌ No fixtures found for "
                f"{team_name} in {CURRENT_SEASON}."
            )

            return

        lines = [
            f"📅 {team_name} — {CURRENT_SEASON}",
            ""
        ]

        count = 0

        for match in matches:

            if count >= 10:
                break

            teams = match.get("teams") or {}

            home = teams.get("home") or {}
            away = teams.get("away") or {}

            home_name = home.get("name", "Home")
            away_name = away.get("name", "Away")

            date_value = (
                match.get("date")
                or match.get("fixture_date")
                or "Unknown date"
            )

            lines.append(
                f"• {date_value}\n"
                f"  {home_name} vs {away_name}"
            )

            count += 1

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as error:

        await update.message.reply_text(
            f"❌ Fixture error:\n{error}"
        )


async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "Usage:\n/analyze Chelsea vs Arsenal"
        )

        return

    text = " ".join(context.args)

    parts = re.split(
        r"\s+vs\s+|\s+v\s+|\s+-\s+",
        text,
        flags=re.IGNORECASE
    )

    if len(parts) != 2:

        await update.message.reply_text(
            "❌ Please use:\n"
            "/analyze Chelsea vs Arsenal"
        )

        return

    team1 = parts[0].strip()
    team2 = parts[1].strip()

    try:

        result = analyze_match(
            team1,
            team2
        )

        await update.message.reply_text(
            result
        )

    except Exception as error:

        await update.message.reply_text(
            f"❌ Analysis error:\n{error}"
        )


async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        openfoot_get(
            "/v1/search",
            {
                "q": "Chelsea"
            }
        )

        await update.message.reply_text(
            "🔧 OPENFOOT TEST PASSED\n\n"
            "OpenFoot API is connected successfully."
        )

    except Exception as error:

        await update.message.reply_text(
            "❌ OPENFOOT TEST FAILED\n\n"
            f"{error}"
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

    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

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

    application.add_handler(
        CommandHandler(
            "apitest",
            apitest_command
        )
    )

    print("GoalLogic AI v2.1 Telegram bot is starting...")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
