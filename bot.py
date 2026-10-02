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


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY")

OPENFOOT_BASE = "https://openfootapi.com"

CURRENT_SEASON = "2026/27"
RECENT_MATCHES = 5

MIN_CONFIDENCE = 50
MAX_CONFIDENCE = 85


# =========================================================
# HEALTH SERVER FOR RENDER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GoalLogic AI v2 is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )
    print(f"GoalLogic AI v2 is running on port {port}.")
    server.serve_forever()


# =========================================================
# OPENFOOT API
# =========================================================

def openfoot_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        raise RuntimeError("OPENFOOT_API_KEY is missing.")

    url = f"{OPENFOOT_BASE}{endpoint}"

    headers = {
        "Authorization": f"Bearer {OPENFOOT_API_KEY}",
        "Accept": "application/json",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params or {},
        timeout=20,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"OpenFoot API error {response.status_code}: "
            f"{response.text[:300]}"
        )

    return response.json()


# =========================================================
# GENERAL HELPERS
# =========================================================

def normalize_name(name):
    return re.sub(
        r"[^a-z0-9]",
        "",
        name.lower(),
    )


def safe_percentage(value):
    return max(0.0, min(100.0, float(value)))


def get_score(match):
    score = match.get("score") or {}

    home = (
        score.get("home")
        if score.get("home") is not None
        else score.get("homeGoals")
    )

    away = (
        score.get("away")
        if score.get("away") is not None
        else score.get("awayGoals")
    )

    if home is None:
        home = match.get("homeScore")

    if away is None:
        away = match.get("awayScore")

    try:
        return int(home), int(away)
    except (TypeError, ValueError):
        return None, None


def team_side(match, team_id):
    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    home_id = home_team.get("id")
    away_id = away_team.get("id")

    if str(home_id) == str(team_id):
        return "home"

    if str(away_id) == str(team_id):
        return "away"

    return None


# =========================================================
# TEAM SEARCH
# =========================================================

def search_team(team_name):
    data = openfoot_get(
        "/v1/search",
        {"q": team_name},
    )

    results = (
        data.get("data")
        or data.get("results")
        or []
    )

    if isinstance(results, dict):
        results = results.get("teams") or []

    if not results:
        return None

    target = normalize_name(team_name)

    # Exact normalized match first
    for team in results:
        name = (
            team.get("name")
            or team.get("teamName")
            or ""
        )

        if normalize_name(name) == target:
            return team

    # Partial match second
    for team in results:
        name = (
            team.get("name")
            or team.get("teamName")
            or ""
        )

        normalized = normalize_name(name)

        if target in normalized or normalized in target:
            return team

    return results[0]


# =========================================================
# RECENT MATCHES
# =========================================================

def get_recent_matches(team_id):
    data = openfoot_get(
        "/v1/matches",
        {
            "team": team_id,
            "season": CURRENT_SEASON,
            "status": "finished",
        },
    )

    matches = (
        data.get("data")
        or data.get("results")
        or []
    )

    if isinstance(matches, dict):
        matches = matches.get("matches") or []

    valid = []

    for match in matches:
        home_goals, away_goals = get_score(match)

        if home_goals is None or away_goals is None:
            continue

        side = team_side(match, team_id)

        if side not in ("home", "away"):
            continue

        valid.append(match)

    # Newest first where possible
    valid.sort(
        key=lambda x: (
            x.get("date")
            or x.get("kickoff")
            or x.get("startTime")
            or ""
        ),
        reverse=True,
    )

    return valid[:RECENT_MATCHES]


# =========================================================
# TEAM STATISTICS
# =========================================================

def calculate_stats(matches, team_id):
    sample = len(matches)

    if sample == 0:
        return {
            "sample": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "wd_rate": 0,
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

    wins = draws = losses = 0
    gf = ga = 0

    over05 = over15 = over25 = 0
    under35 = btts = 0
    scoring = clean_sheet = 0

    for match in matches:
        home_goals, away_goals = get_score(match)
        side = team_side(match, team_id)

        if side == "home":
            scored = home_goals
            conceded = away_goals
        else:
            scored = away_goals
            conceded = home_goals

        gf += scored
        ga += conceded

        total = scored + conceded

        if scored > conceded:
            wins += 1
        elif scored == conceded:
            draws += 1
        else:
            losses += 1

        if total >= 1:
            over05 += 1

        if total >= 2:
            over15 += 1

        if total >= 3:
            over25 += 1

        if total <= 3:
            under35 += 1

        if scored > 0 and conceded > 0:
            btts += 1

        if scored > 0:
            scoring += 1

        if conceded == 0:
            clean_sheet += 1

    return {
        "sample": sample,
        "wins": wins,
        "draws": draws,
        "losses": losses,

        "wd_rate": ((wins + draws) / sample) * 100,
        "draw_rate": (draws / sample) * 100,
        "loss_rate": (losses / sample) * 100,

        "gf": gf,
        "ga": ga,

        "avg_scored": gf / sample,
        "avg_conceded": ga / sample,
        "avg_total": (gf + ga) / sample,

        "over05": (over05 / sample) * 100,
        "over15": (over15 / sample) * 100,
        "over25": (over25 / sample) * 100,
        "under35": (under35 / sample) * 100,

        "btts": (btts / sample) * 100,
        "scoring": (scoring / sample) * 100,
        "clean_sheet": (clean_sheet / sample) * 100,
    }


# =========================================================
# HOME / AWAY CONTEXT
# =========================================================

def venue_stats(matches, team_id, venue):
    selected = []

    for match in matches:
        side = team_side(match, team_id)

        if side == venue:
            selected.append(match)

    sample = len(selected)

    if sample == 0:
        return {
            "sample": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "wd": 50,
            "scoring": 50,
            "clean_sheet": 50,
            "over25": 50,
            "btts": 50,
            "avg_scored": 0,
            "avg_conceded": 0,
        }

    stats = calculate_stats(
        selected,
        team_id,
    )

    return {
        "sample": sample,
        "wins": stats["wins"],
        "draws": stats["draws"],
        "losses": stats["losses"],
        "wd": stats["wd_rate"],
        "scoring": stats["scoring"],
        "clean_sheet": stats["clean_sheet"],
        "over25": stats["over25"],
        "btts": stats["btts"],
        "avg_scored": stats["avg_scored"],
        "avg_conceded": stats["avg_conceded"],
    }


# =========================================================
# CONFIDENCE ENGINE
# =========================================================

def sample_weight(sample):
    if sample <= 0:
        return 0.30

    if sample == 1:
        return 0.40

    if sample == 2:
        return 0.55

    if sample == 3:
        return 0.70

    if sample == 4:
        return 0.85

    return 1.00


def confidence(
    base,
    supports=0,
    contradictions=0,
    sample=5,
):
    """
    Central confidence model.

    The base signal is deliberately pulled toward 50%.
    Small samples are weighted heavily toward uncertainty.
    Supporting evidence adds confidence.
    Contradictory evidence removes more confidence.
    """

    weight = sample_weight(sample)

    adjusted = 50 + (
        (base - 50)
        * 0.50
        * weight
    )

    adjusted += supports * 1.25
    adjusted -= contradictions * 2.50

    # Additional reliability penalty
    if sample < 3:
        adjusted -= 4

    elif sample == 3:
        adjusted -= 2

    return round(
        max(
            MIN_CONFIDENCE,
            min(MAX_CONFIDENCE, adjusted),
        )
    )


def grade(value):
    if value >= 78:
        return "🔥 STRONG"

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


def market_line(name, value):
    return (
        f"{name}: {value}% — "
        f"{grade(value)} — "
        f"{advice(value)}"
    )


# =========================================================
# MARKET ENGINE
# =========================================================

def calculate_markets(
    team1,
    team2,
    s1,
    s2,
    home,
    away,
):
    markets = {}

    # -----------------------------------------------------
    # 1X
    # -----------------------------------------------------

    base_1x = (
        s1["wd_rate"] * 0.50
        + s2["loss_rate"] * 0.25
        + home["wd"] * 0.25
    )

    support = 0
    contradiction = 0

    if s1["wd_rate"] >= 70:
        support += 1

    if home["wd"] >= 70 and home["sample"] >= 2:
        support += 1

    if s2["loss_rate"] >= 50:
        support += 1

    if home["sample"] >= 2 and home["wd"] <= 50:
        contradiction += 1

    if s1["loss_rate"] >= 50:
        contradiction += 1

    markets["1X"] = confidence(
        base_1x,
        support,
        contradiction,
        min(s1["sample"], home["sample"] or s1["sample"]),
    )

    # -----------------------------------------------------
    # X2
    # -----------------------------------------------------

    base_x2 = (
        s2["wd_rate"] * 0.50
        + s1["loss_rate"] * 0.25
        + away["wd"] * 0.25
    )

    support = 0
    contradiction = 0

    if s2["wd_rate"] >= 70:
        support += 1

    if away["wd"] >= 70 and away["sample"] >= 2:
        support += 1

    if s1["loss_rate"] >= 50:
        support += 1

    if away["sample"] >= 2 and away["wd"] <= 50:
        contradiction += 1

    if s2["loss_rate"] >= 50:
        contradiction += 1

    markets["X2"] = confidence(
        base_x2,
        support,
        contradiction,
        min(s2["sample"], away["sample"] or s2["sample"]),
    )

    # -----------------------------------------------------
    # 12 — NO DRAW
    # -----------------------------------------------------

    base_12 = (
        (100 - ((s1["draw_rate"] + s2["draw_rate"]) / 2))
        * 0.70
        + ((home["wd"] + away["wd"]) / 2)
        * 0.30
    )

    support = 0
    contradiction = 0

    if s1["draw_rate"] <= 20:
        support += 1

    if s2["draw_rate"] <= 20:
        support += 1

    if s1["draw_rate"] >= 40 or s2["draw_rate"] >= 40:
        contradiction += 1

    markets["12"] = confidence(
        base_12,
        support,
        contradiction,
        min(s1["sample"], s2["sample"]),
    )

    # -----------------------------------------------------
    # OVER 0.5
    # -----------------------------------------------------

    base_o05 = (
        (s1["over05"] + s2["over05"]) / 2
    )

    support = 0
    contradiction = 0

    if s1["over05"] >= 80 and s2["over05"] >= 80:
        support += 1

    if (
        home["sample"] >= 2
        and away["sample"] >= 2
        and home["over25"] >= 50
        and away["over25"] >= 50
    ):
        support += 1

    # Do NOT treat Over2.5 as the same thing as Over0.5.
    # Only strong low-scoring evidence should contradict it.

    if s1["over05"] <= 60:
        contradiction += 1

    if s2["over05"] <= 60:
        contradiction += 1

    markets["Over 0.5"] = confidence(
        base_o05,
        support,
        contradiction,
        min(s1["sample"], s2["sample"]),
    )

    # -----------------------------------------------------
    # OVER 1.5
    # -----------------------------------------------------

    base_o15 = (
        (s1["over15"] + s2["over15"]) / 2
    )

    support = 0
    contradiction = 0

    if s1["over15"] >= 70 and s2["over15"] >= 70:
        support += 1

    if s1["avg_total"] >= 2.5 and s2["avg_total"] >= 2.5:
        support += 1

    if s1["over15"] <= 40:
        contradiction += 1

    if s2["over15"] <= 40:
        contradiction += 1

    markets["Over 1.5"] = confidence(
        base_o15,
        support,
        contradiction,
        min(s1["sample"], s2["sample"]),
    )

    # -----------------------------------------------------
    # OVER 2.5
    # -----------------------------------------------------

    base_o25 = (
        (s1["over25"] + s2["over25"]) / 2
    )

    support = 0
    contradiction = 0

    if s1["over25"] >= 70 and s2["over25"] >= 70:
        support += 2

    elif s1["over25"] >= 60 and s2["over25"] >= 60:
        support += 1

    if s1["over25"] <= 40:
        contradiction += 1

    if s2["over25"] <= 40:
        contradiction += 1

    if home["sample"] >= 2 and home["over25"] <= 25:
        contradiction += 1

    if away["sample"] >= 2 and away["over25"] <= 25:
        contradiction += 1

    markets["Over 2.5"] = confidence(
        base_o25,
        support,
        contradiction,
        min(s1["sample"], s2["sample"]),
    )

    # -----------------------------------------------------
    # UNDER 3.5
    # -----------------------------------------------------

    base_u35 = (
        (s1["under35"] + s2["under35"]) / 2
    )

    support = 0
    contradiction = 0

    if s1["under35"] >= 70 and s2["under35"] >= 70:
        support += 2

    elif s1["under35"] >= 60 and s2["under35"] >= 60:
        support += 1

    if s1["avg_total"] >= 3.20:
        contradiction += 1

    if s2["avg_total"] >= 3.20:
        contradiction += 1

    if home["sample"] >= 2 and home["over25"] >= 75:
        contradiction += 1

    if away["sample"] >= 2 and away["over25"] >= 75:
        contradiction += 1

    markets["Under 3.5"] = confidence(
        base_u35,
        support,
        contradiction,
        min(s1["sample"], s2["sample"]),
    )

    # -----------------------------------------------------
    # BTTS YES
    # -----------------------------------------------------

    base_btts = (
        (s1["btts"] + s2["btts"]) / 2
    )

    support = 0
    contradiction = 0

    if s1["btts"] >= 70 and s2["btts"] >= 70:
        support += 2

    elif s1["btts"] >= 60 and s2["btts"] >= 60:
        support += 1

    if s1["btts"] <= 40:
        contradiction += 1

    if s2["btts"] <= 40:
        contradiction += 1

    if home["sample"] >= 2 and home["btts"] == 0:
        contradiction += 1

    if away["sample"] >= 2 and away["btts"] == 0:
        contradiction += 1

    markets["BTTS Yes"] = confidence(
        base_btts,
        support,
        contradiction,
        min(s1["sample"], s2["sample"]),
    )

    # -----------------------------------------------------
    # TEAM 1 TO SCORE
    # -----------------------------------------------------

    attack_1 = s1["scoring"]
    opponent_defense_1 = 100 - s2["clean_sheet"]

    venue_attack_1 = home["scoring"]

    base_team1 = (
        attack_1 * 0.40
        + opponent_defense_1 * 0.35
        + venue_attack_1 * 0.25
    )

    support = 0
    contradiction = 0

    if attack_1 >= 70:
        support += 1

    if opponent_defense_1 >= 70:
        support += 1

    if home["sample"] >= 2 and venue_attack_1 >= 70:
        support += 1

    if home["sample"] >= 2 and venue_attack_1 <= 25:
        contradiction += 2

    if s2["clean_sheet"] >= 60:
        contradiction += 1

    if attack_1 <= 40:
        contradiction += 1

    markets[f"{team1} to score"] = confidence(
        base_team1,
        support,
        contradiction,
        min(s1["sample"], home["sample"] or s1["sample"]),
    )

    # -----------------------------------------------------
    # TEAM 2 TO SCORE
    # -----------------------------------------------------

    attack_2 = s2["scoring"]
    opponent_defense_2 = 100 - s1["clean_sheet"]

    venue_attack_2 = away["scoring"]

    base_team2 = (
        attack_2 * 0.40
        + opponent_defense_2 * 0.35
        + venue_attack_2 * 0.25
    )

    support = 0
    contradiction = 0

    if attack_2 >= 70:
        support += 1

    if opponent_defense_2 >= 70:
        support += 1

    if away["sample"] >= 2 and venue_attack_2 >= 70:
        support += 1

    if away["sample"] >= 2 and venue_attack_2 <= 25:
        contradiction += 2

    if s1["clean_sheet"] >= 60:
        contradiction += 1

    if attack_2 <= 40:
        contradiction += 1

    markets[f"{team2} to score"] = confidence(
        base_team2,
        support,
        contradiction,
        min(s2["sample"], away["sample"] or s2["sample"]),
    )

    return markets


# =========================================================
# PRIMARY SIGNAL
# =========================================================

def choose_primary(markets):
    candidates = []

    for name, value in markets.items():
        if value >= 68:
            candidates.append(
                (value, name)
            )

    if not candidates:
        return None

    # Highest supported confidence.
    candidates.sort(
        reverse=True
    )

    return candidates[0]


# =========================================================
# FORMATTING
# =========================================================

def format_team_stats(name, stats):
    form = (
        "W" * stats["wins"]
        + "D" * stats["draws"]
        + "L" * stats["losses"]
    )

    return (
        f"📊 {name.upper()} — LAST {stats['sample']}\n"
        f"Form: {form}\n"
        f"W/D/L: "
        f"{stats['wins']}/"
        f"{stats['draws']}/"
        f"{stats['losses']}\n"
        f"Goals scored: {stats['gf']}\n"
        f"Goals conceded: {stats['ga']}\n"
        f"Avg scored: {stats['avg_scored']:.2f}\n"
        f"Avg conceded: {stats['avg_conceded']:.2f}\n"
        f"Avg total goals: {stats['avg_total']:.2f}\n"
        f"Over 0.5: {stats['over05']:.0f}%\n"
        f"Over 1.5: {stats['over15']:.0f}%\n"
        f"Over 2.5: {stats['over25']:.0f}%\n"
        f"Under 3.5: {stats['under35']:.0f}%\n"
        f"BTTS: {stats['btts']:.0f}%\n"
        f"Scoring consistency: {stats['scoring']:.0f}%\n"
        f"Clean sheets: {stats['clean_sheet']:.0f}%"
    )


def format_venue(name, context, venue):
    if context["sample"] == 0:
        return (
            f"{name} {venue.upper()}: "
            f"No separate venue sample available."
        )

    return (
        f"{name} {venue.upper()} — "
        f"Sample {context['sample']}\n"
        f"W/D/L: "
        f"{context['wins']}/"
        f"{context['draws']}/"
        f"{context['losses']}\n"
        f"Scoring: {context['scoring']:.0f}%\n"
        f"Clean sheets: {context['clean_sheet']:.0f}%\n"
        f"Over 2.5: {context['over25']:.0f}%\n"
        f"BTTS: {context['btts']:.0f}%"
    )


# =========================================================
# MATCH ANALYSIS
# =========================================================

def analyze_match(team1_name, team2_name):
    team1 = search_team(team1_name)
    team2 = search_team(team2_name)

    if not team1:
        return f"❌ Could not find team: {team1_name}"

    if not team2:
        return f"❌ Could not find team: {team2_name}"

    team1_id = team1.get("id")
    team2_id = team2.get("id")

    if not team1_id or not team2_id:
        return "❌ Could not identify one of the teams."

    matches1 = get_recent_matches(team1_id)
    matches2 = get_recent_matches(team2_id)

    if len(matches1) < 3:
        return (
            f"❌ Not enough completed matches for "
            f"{team1_name}. Found {len(matches1)}."
        )

    if len(matches2) < 3:
        return (
            f"❌ Not enough completed matches for "
            f"{team2_name}. Found {len(matches2)}."
        )

    stats1 = calculate_stats(
        matches1,
        team1_id,
    )

    stats2 = calculate_stats(
        matches2,
        team2_id,
    )

    home = venue_stats(
        matches1,
        team1_id,
        "home",
    )

    away = venue_stats(
        matches2,
        team2_id,
        "away",
    )

    markets = calculate_markets(
        team1_name,
        team2_name,
        stats1,
        stats2,
        home,
        away,
    )

    primary = choose_primary(markets)

    lines = [
        "⚽ GOALLOGIC AI v2 — MATCH ANALYSIS",
        "",
        f"🏟️ {team1_name} vs {team2_name}",
        f"Season: {CURRENT_SEASON}",
        f"Sample: Last {RECENT_MATCHES} available matches",
        "",
        format_team_stats(
            team1_name,
            stats1,
        ),
        "",
        format_team_stats(
            team2_name,
            stats2,
        ),
        "",
        "🏠 HOME / ✈️ AWAY CONTEXT",
        "",
        format_venue(
            team1_name,
            home,
            "home",
        ),
        "",
        format_venue(
            team2_name,
            away,
            "away",
        ),
        "",
        "📈 MARKET CONFIDENCE",
        "",
        market_line(
            "1X",
            markets["1X"],
        ),
        market_line(
            "X2",
            markets["X2"],
        ),
        market_line(
            "12",
            markets["12"],
        ),
        "",
        market_line(
            "Over 0.5",
            markets["Over 0.5"],
        ),
        market_line(
            "Over 1.5",
            markets["Over 1.5"],
        ),
        market_line(
            "Over 2.5",
            markets["Over 2.5"],
        ),
        market_line(
            "Under 3.5",
            markets["Under 3.5"],
        ),
        "",
        market_line(
            "BTTS Yes",
            markets["BTTS Yes"],
        ),
        "",
        market_line(
            f"{team1_name} to score",
            markets[f"{team1_name} to score"],
        ),
        market_line(
            f"{team2_name} to score",
            markets[f"{team2_name} to score"],
        ),
        "",
    ]

    if primary:
        value, market = primary

        lines.extend([
            "🎯 PRIMARY SIGNAL",
            f"{market}",
            f"Confidence: {value}%",
            f"Grade: {grade(value)}",
            f"Advice: {advice(value)}",
        ])
    else:
        lines.extend([
            "🎯 PRIMARY SIGNAL",
            "No market reached the 68% confidence threshold.",
            "Advice: AVOID / WAIT",
        ])

    lines.extend([
        "",
        "🧠 ENGINE NOTE",
        "Confidence combines recent form, attack, "
        "defence, goal environment, home/away evidence "
        "and sample-size reliability.",
        "",
        "⚠️ Confidence figures are statistical indicators, "
        "not guarantees of the match result.",
    ])

    return "\n".join(lines)


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "⚽ Welcome to GoalLogic AI v2!\n\n"
        "I analyze football matches using recent "
        "historical statistics.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
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

    try:
        data = openfoot_get(
            "/v1/search",
            {"q": query},
        )

        results = (
            data.get("data")
            or data.get("results")
            or []
        )

        if isinstance(results, dict):
            results = results.get("teams") or []

        if not results:
            await update.message.reply_text(
                f"❌ No results found for {query}"
            )
            return

        lines = [
            f"🔎 Search results for: {query}",
            "",
        ]

        for team in results[:8]:
            name = (
                team.get("name")
                or team.get("teamName")
                or "Unknown"
            )

            team_id = team.get("id", "?")

            country = (
                team.get("country")
                or team.get("countryName")
                or ""
            )

            extra = (
                f" — {country}"
                if country
                else ""
            )

            lines.append(
                f"• {name} — ID {team_id}{extra}"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as exc:
        await update.message.reply_text(
            f"❌ Error: {exc}"
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

    try:
        team = search_team(query)

        if not team:
            await update.message.reply_text(
                f"❌ Team not found: {query}"
            )
            return

        team_id = team.get("id")

        data = openfoot_get(
            "/v1/matches",
            {
                "team": team_id,
                "season": CURRENT_SEASON,
            },
        )

        matches = (
            data.get("data")
            or data.get("results")
            or []
        )

        if isinstance(matches, dict):
            matches = matches.get("matches") or []

        if not matches:
            await update.message.reply_text(
                f"❌ No fixtures found for {query}."
            )
            return

        lines = [
            f"📅 {query} — {CURRENT_SEASON}",
            "",
        ]

        for match in matches[:10]:
            home = (
                (match.get("homeTeam") or {}).get("name")
                or "Home"
            )

            away = (
                (match.get("awayTeam") or {}).get("name")
                or "Away"
            )

            date = (
                match.get("date")
                or match.get("kickoff")
                or match.get("startTime")
                or "Date unavailable"
            )

            lines.append(
                f"• {date}\n"
                f"  {home} vs {away}"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as exc:
        await update.message.reply_text(
            f"❌ Error: {exc}"
        )


async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    text = " ".join(context.args)

    parts = re.split(
        r"\s+vs\.?\s+|\s+v\.?\s+|\s+-\s+",
        text,
        flags=re.IGNORECASE,
    )

    if len(parts) != 2:
        await update.message.reply_text(
            "Please use:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    team1 = parts[0].strip()
    team2 = parts[1].strip()

    await update.message.reply_text(
        "🔎 Analyzing the available data..."
    )

    try:
        result = analyze_match(
            team1,
            team2,
        )

        await update.message.reply_text(
            result
        )

    except Exception as exc:
        await update.message.reply_text(
            f"❌ Analysis error:\n{exc}"
        )


async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        openfoot_get(
            "/v1/search",
            {"q": "Chelsea"},
        )

        await update.message.reply_text(
            "🔧 OPENFOOT TEST PASSED\n\n"
            "OpenFoot API is connected successfully."
        )

    except Exception as exc:
        await update.message.reply_text(
            "❌ OPENFOOT TEST FAILED\n\n"
            f"{exc}"
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

    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

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

    application.add_handler(
        CommandHandler(
            "apitest",
            apitest_command,
        )
    )

    print("GoalLogic AI v2 Telegram bot is starting...")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
