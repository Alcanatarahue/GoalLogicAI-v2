import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from statistics import mean

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


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
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GoalLogic AI is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", 10000))

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"GoalLogic AI health server running on port {port}")

    server.serve_forever()


# =========================================================
# OPENFOOT API
# =========================================================

def openfoot_get(endpoint, params=None):

    if not OPENFOOT_API_KEY:
        raise Exception("OPENFOOT_API_KEY is missing.")

    headers = {
        "Authorization": f"Bearer {OPENFOOT_API_KEY}",
        "Accept": "application/json",
    }

    response = requests.get(
        OPENFOOT_BASE + endpoint,
        headers=headers,
        params=params,
        timeout=20,
    )

    print("OPENFOOT STATUS:", response.status_code)

    response.raise_for_status()

    return response.json()


# =========================================================
# HELPERS
# =========================================================

def normalize_name(name):

    if not name:
        return ""

    return str(name).lower().strip()


def percentage(value):

    try:
        return round(float(value))
    except Exception:
        return 0


def clamp(value, minimum=MIN_CONFIDENCE, maximum=MAX_CONFIDENCE):

    return max(minimum, min(maximum, round(value)))


# =========================================================
# TEAM SEARCH
# =========================================================

def search_team(team_name):

    data = openfoot_get(
        "/v1/search",
        params={"q": team_name},
    )

    if isinstance(data, list):
        results = data

    elif isinstance(data, dict):

        results = (
            data.get("data")
            or data.get("results")
            or data.get("teams")
            or data.get("items")
            or []
        )

    else:
        results = []

    if not results:
        return None

    wanted = normalize_name(team_name)

    # Exact match
    for team in results:

        name = (
            team.get("name")
            or team.get("team_name")
            or team.get("display_name")
            or ""
        )

        if normalize_name(name) == wanted:
            return team

    # Partial match
    for team in results:

        name = (
            team.get("name")
            or team.get("team_name")
            or ""
        )

        if wanted in normalize_name(name):
            return team

    return results[0]


def team_id(team):

    return (
        team.get("id")
        or team.get("team_id")
        or team.get("teamId")
    )


def team_display_name(team):

    return (
        team.get("name")
        or team.get("team_name")
        or team.get("display_name")
        or "Unknown"
    )


# =========================================================
# OPENFOOT MATCH PARSER
# =========================================================

def extract_matches(data):

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        return (
            data.get("data")
            or data.get("matches")
            or data.get("fixtures")
            or data.get("results")
            or data.get("items")
            or []
        )

    return []


def parse_match(match, requested_team_id):

    """
    OpenFoot actual structure:

    homeTeam
    awayTeam
    kickoffAt
    score.home
    score.away
    """

    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    home_id = home_team.get("id")
    away_id = away_team.get("id")

    score = match.get("score") or {}

    home_score = score.get("home")
    away_score = score.get("away")

    if home_score is None or away_score is None:
        return None

    try:
        home_score = int(home_score)
        away_score = int(away_score)
    except Exception:
        return None

    if str(home_id) == str(requested_team_id):

        return {
            "gf": home_score,
            "ga": away_score,
            "venue": "home",
            "date": match.get("kickoffAt"),
            "home_team": home_team.get("name", "Home"),
            "away_team": away_team.get("name", "Away"),
        }

    if str(away_id) == str(requested_team_id):

        return {
            "gf": away_score,
            "ga": home_score,
            "venue": "away",
            "date": match.get("kickoffAt"),
            "home_team": home_team.get("name", "Home"),
            "away_team": away_team.get("name", "Away"),
        }

    return None


# =========================================================
# RECENT MATCHES
# =========================================================

def get_recent_matches(team_id_value):

    data = openfoot_get(
        "/v1/matches",
        params={
            "team": team_id_value,
            "season": CURRENT_SEASON,
            "status": "finished",
        },
    )

    raw_matches = extract_matches(data)

    parsed = []

    for match in raw_matches:

        result = parse_match(
            match,
            team_id_value
        )

        if result:
            parsed.append(result)

    # Most recent first
    parsed.sort(
        key=lambda x: x.get("date") or "",
        reverse=True
    )

    return parsed[:RECENT_MATCHES]


# =========================================================
# STATISTICS
# =========================================================

def calculate_stats(matches):

    if not matches:
        return {
            "sample": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "avg_gf": 0,
            "avg_ga": 0,
            "scoring": 0,
            "clean_sheet": 0,
            "over25": 0,
            "under35": 0,
            "btts": 0,
        }

    wins = sum(
        1 for m in matches
        if m["gf"] > m["ga"]
    )

    draws = sum(
        1 for m in matches
        if m["gf"] == m["ga"]
    )

    losses = sum(
        1 for m in matches
        if m["gf"] < m["ga"]
    )

    gf = sum(m["gf"] for m in matches)
    ga = sum(m["ga"] for m in matches)

    total_goals = [
        m["gf"] + m["ga"]
        for m in matches
    ]

    scoring = percentage(
        sum(m["gf"] > 0 for m in matches)
        / len(matches)
        * 100
    )

    clean_sheet = percentage(
        sum(m["ga"] == 0 for m in matches)
        / len(matches)
        * 100
    )

    over25 = percentage(
        sum(g > 2 for g in total_goals)
        / len(matches)
        * 100
    )

    under35 = percentage(
        sum(g < 4 for g in total_goals)
        / len(matches)
        * 100
    )

    btts = percentage(
        sum(
            m["gf"] > 0 and m["ga"] > 0
            for m in matches
        )
        / len(matches)
        * 100
    )

    return {
        "sample": len(matches),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf,
        "ga": ga,
        "avg_gf": round(gf / len(matches), 2),
        "avg_ga": round(ga / len(matches), 2),
        "scoring": scoring,
        "clean_sheet": clean_sheet,
        "over25": over25,
        "under35": under35,
        "btts": btts,
    }


# =========================================================
# VENUE STATISTICS
# =========================================================

def venue_stats(matches, venue):

    selected = [
        m for m in matches
        if m["venue"] == venue
    ]

    if not selected:
        return {
            "sample": 0,
            "scoring": 0,
            "clean_sheet": 0,
            "over25": 0,
            "btts": 0,
        }

    return {
        "sample": len(selected),

        "scoring": percentage(
            sum(m["gf"] > 0 for m in selected)
            / len(selected)
            * 100
        ),

        "clean_sheet": percentage(
            sum(m["ga"] == 0 for m in selected)
            / len(selected)
            * 100
        ),

        "over25": percentage(
            sum(
                m["gf"] + m["ga"] > 2
                for m in selected
            )
            / len(selected)
            * 100
        ),

        "btts": percentage(
            sum(
                m["gf"] > 0 and m["ga"] > 0
                for m in selected
            )
            / len(selected)
            * 100
        ),
    }


# =========================================================
# FORM STRING
# =========================================================

def form_string(matches):

    form = []

    for match in matches:

        if match["gf"] > match["ga"]:
            form.append("W")

        elif match["gf"] == match["ga"]:
            form.append("D")

        else:
            form.append("L")

    return "".join(form)


# =========================================================
# CONFIDENCE MODEL
# =========================================================

def sample_reliability(sample):

    if sample <= 1:
        return 0.20

    if sample == 2:
        return 0.35

    if sample == 3:
        return 0.55

    if sample == 4:
        return 0.75

    return 1.00


def confidence(
    base,
    sample,
    support=0,
    contradiction=0,
):

    reliability = sample_reliability(sample)

    adjusted = (
        50
        + (base - 50)
        * 0.55
        * reliability
    )

    adjusted += support * 2
    adjusted -= contradiction * 4

    # Conservative small-sample caps
    if sample <= 1:
        adjusted = min(adjusted, 58)

    elif sample == 2:
        adjusted = min(adjusted, 62)

    elif sample == 3:
        adjusted = min(adjusted, 67)

    elif sample == 4:
        adjusted = min(adjusted, 73)

    return clamp(adjusted)


# =========================================================
# MARKET ENGINE
# =========================================================

def analyze_markets(
    team1_name,
    team2_name,
    s1,
    s2,
    home1,
    away2,
):

    markets = {}

    # -----------------------------------------------------
    # 1X
    # -----------------------------------------------------

    base_1x = (
        50
        + (s1["wins"] / s1["sample"]) * 25
        + (s2["losses"] / s2["sample"]) * 10
    )

    support = 0
    contradiction = 0

    if home1["sample"] >= 2:

        if home1["scoring"] >= 67:
            support += 1

        if home1["clean_sheet"] >= 50:
            support += 1

    if away2["sample"] >= 2:
        if away2["wins"] == 0:
            support += 1

    markets["1X"] = confidence(
        base_1x,
        min(s1["sample"], 5),
        support,
        contradiction,
    )

    # -----------------------------------------------------
    # X2
    # -----------------------------------------------------

    base_x2 = (
        50
        + (s2["wins"] / s2["sample"]) * 25
        + (s1["losses"] / s1["sample"]) * 10
    )

    support = 0
    contradiction = 0

    if away2["sample"] >= 2:

        if away2["clean_sheet"] >= 50:
            support += 1

        if away2["scoring"] >= 67:
            support += 1

    # Strong home performance against X2
    if home1["sample"] >= 2:

        if home1["wins"] >= 1:
            contradiction += 1

    markets["X2"] = confidence(
        base_x2,
        min(s2["sample"], 5),
        support,
        contradiction,
    )

    # -----------------------------------------------------
    # OVER 1.5
    # -----------------------------------------------------

    base_o15 = (
        50
        + ((s1["over25"] + s2["over25"]) / 2 - 50)
        * 0.45
    )

    support = 0
    contradiction = 0

    if s1["avg_gf"] + s2["avg_gf"] >= 2:
        support += 1

    if home1["sample"] >= 2:

        if home1["over25"] >= 50:
            support += 1

        if home1["over25"] == 0:
            contradiction += 1

    if away2["sample"] >= 2:

        if away2["over25"] == 0:
            contradiction += 1

    markets["O1.5"] = confidence(
        base_o15,
        min(
            s1["sample"],
            s2["sample"],
            5
        ),
        support,
        contradiction,
    )

    # -----------------------------------------------------
    # OVER 2.5
    # -----------------------------------------------------

    base_o25 = (
        50
        + ((s1["over25"] + s2["over25"]) / 2 - 50)
        * 0.50
    )

    support = 0
    contradiction = 0

    if home1["sample"] >= 2:

        if home1["over25"] >= 67:
            support += 1

        if home1["over25"] == 0:
            contradiction += 1

    if away2["sample"] >= 2:

        if away2["over25"] >= 67:
            support += 1

        if away2["over25"] == 0:
            contradiction += 1

    markets["O2.5"] = confidence(
        base_o25,
        min(
            s1["sample"],
            s2["sample"],
            5
        ),
        support,
        contradiction,
    )

    # -----------------------------------------------------
    # UNDER 3.5
    # -----------------------------------------------------

    under_signal = (
        s1["under35"] + s2["under35"]
    ) / 2

    base_u35 = 50 + (
        under_signal - 50
    ) * 0.45

    support = 0
    contradiction = 0

    if home1["sample"] >= 2:

        if home1["over25"] == 0:
            support += 1

    if away2["sample"] >= 2:

        if away2["over25"] == 0:
            support += 1

    if s1["over25"] >= 80:
        contradiction += 1

    if s2["over25"] >= 80:
        contradiction += 1

    markets["U3.5"] = confidence(
        base_u35,
        min(
            s1["sample"],
            s2["sample"],
            5
        ),
        support,
        contradiction,
    )

    # -----------------------------------------------------
    # BTTS
    # -----------------------------------------------------

    base_btts = (
        50
        + ((s1["btts"] + s2["btts"]) / 2 - 50)
        * 0.45
    )

    support = 0
    contradiction = 0

    if home1["sample"] >= 2:

        if home1["scoring"] == 100:
            support += 1

        if home1["btts"] >= 50:
            support += 1

        if home1["btts"] == 0:
            contradiction += 1

    if away2["sample"] >= 2:

        if away2["scoring"] == 0:
            contradiction += 2

        if away2["btts"] == 0:
            contradiction += 1

    markets["BTTS"] = confidence(
        base_btts,
        min(
            s1["sample"],
            s2["sample"],
            5
        ),
        support,
        contradiction,
    )

    # -----------------------------------------------------
    # TEAM 1 TO SCORE
    # -----------------------------------------------------

    base_team1 = (
        s1["scoring"] * 0.55
        + (100 - away2["clean_sheet"]) * 0.45
    )

    support = 0
    contradiction = 0

    if home1["sample"] >= 2:

        if home1["scoring"] >= 67:
            support += 1

        if home1["scoring"] == 0:
            contradiction += 3

    if away2["sample"] >= 2:

        if away2["clean_sheet"] >= 67:
            contradiction += 2

    markets[f"{team1_name} score"] = confidence(
        base_team1,
        min(
            s1["sample"],
            away2["sample"] or s1["sample"],
            5
        ),
        support,
        contradiction,
    )

    # -----------------------------------------------------
    # TEAM 2 TO SCORE
    # -----------------------------------------------------

    base_team2 = (
        s2["scoring"] * 0.55
        + (100 - home1["clean_sheet"]) * 0.45
    )

    support = 0
    contradiction = 0

    if away2["sample"] >= 2:

        if away2["scoring"] >= 67:
            support += 1

        if away2["scoring"] == 0:
            contradiction += 3

    if home1["sample"] >= 2:

        if home1["clean_sheet"] >= 67:
            contradiction += 2

    markets[f"{team2_name} score"] = confidence(
        base_team2,
        min(
            s2["sample"],
            home1["sample"] or s2["sample"],
            5
        ),
        support,
        contradiction,
    )

    return markets


# =========================================================
# PRIMARY MARKET
# =========================================================

def choose_primary(markets):

    candidates = []

    penalties = {
        "O1.5": 2,
        "O2.5": 0,
        "U3.5": 0,
        "BTTS": 1,
        "1X": 1,
        "X2": 1,
    }

    for market, value in markets.items():

        if value < 68:
            continue

        penalty = penalties.get(market, 0)

        score = value - penalty

        candidates.append(
            (score, market, value)
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return candidates[0]


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "⚽ Welcome to GoalLogic AI!\n\n"
        "I analyze football matches using historical statistics.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )


# =========================================================
# /APITEST
# =========================================================

async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        openfoot_get(
            "/v1/search",
            params={"q": "Sunderland"},
        )

        await update.message.reply_text(
            "🔧 OPENFOOT TEST PASSED\n\n"
            "OpenFoot API is connected successfully."
        )

    except Exception as e:

        await update.message.reply_text(
            "❌ OPENFOOT TEST FAILED\n\n"
            f"{str(e)[:2500]}"
        )


# =========================================================
# /TEAM
# =========================================================

async def team_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Use:\n/team Sunderland"
        )

        return

    name = " ".join(context.args)

    try:

        team = search_team(name)

        if not team:

            await update.message.reply_text(
                f"❌ No team found for {name}."
            )

            return

        await update.message.reply_text(
            "⚽ TEAM SEARCH\n\n"
            f"Team: {team_display_name(team)}\n"
            f"ID: {team_id(team)}\n"
            f"Country: {team.get('country', 'Unknown')}"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error:\n{str(e)[:2500]}"
        )


# =========================================================
# /FIXTURES
# =========================================================

async def fixtures_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Use:\n/fixtures Sunderland"
        )

        return

    name = " ".join(context.args)

    try:

        team = search_team(name)

        if not team:

            await update.message.reply_text(
                f"❌ No team found for {name}."
            )

            return

        team_id_value = team_id(team)
        team_name = team_display_name(team)

        data = openfoot_get(
            "/v1/matches",
            params={
                "team": team_id_value,
                "season": CURRENT_SEASON,
            },
        )

        matches = extract_matches(data)

        if not matches:

            await update.message.reply_text(
                f"❌ No fixtures found for {team_name}."
            )

            return

        lines = [
            f"📅 {team_name} — {CURRENT_SEASON}",
            ""
        ]

        for match in matches[:10]:

            kickoff = (
                match.get("kickoffAt")
                or "Unknown date"
            )

            home = match.get("homeTeam") or {}
            away = match.get("awayTeam") or {}

            home_name = home.get(
                "name",
                "Home"
            )

            away_name = away.get(
                "name",
                "Away"
            )

            lines.append(
                f"• {kickoff}\n"
                f"  {home_name} vs {away_name}"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error:\n{str(e)[:2500]}"
        )


# =========================================================
# /ANALYZE
# =========================================================

async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Use:\n"
            "/analyze Sunderland vs Arsenal"
        )

        return

    query = " ".join(context.args)

    parts = query.split(" vs ")

    if len(parts) != 2:

        parts = query.lower().split(" vs ")

        if len(parts) != 2:

            await update.message.reply_text(
                "Please use:\n"
                "/analyze Sunderland vs Arsenal"
            )

            return

    team1_search = parts[0].strip()
    team2_search = parts[1].strip()

    try:

        team1 = search_team(team1_search)
        team2 = search_team(team2_search)

        if not team1 or not team2:

            await update.message.reply_text(
                "❌ I could not find both teams."
            )

            return

        team1_id = team_id(team1)
        team2_id = team_id(team2)

        team1_name = team_display_name(team1)
        team2_name = team_display_name(team2)

        matches1 = get_recent_matches(team1_id)
        matches2 = get_recent_matches(team2_id)

        if not matches1:

            await update.message.reply_text(
                f"❌ Not enough recent data for "
                f"{team1_name}."
            )

            return

        if not matches2:

            await update.message.reply_text(
                f"❌ Not enough recent data for "
                f"{team2_name}."
            )

            return

        stats1 = calculate_stats(matches1)
        stats2 = calculate_stats(matches2)

        home1 = venue_stats(
            matches1,
            "home"
        )

        away2 = venue_stats(
            matches2,
            "away"
        )

        markets = analyze_markets(
            team1_name,
            team2_name,
            stats1,
            stats2,
            home1,
            away2,
        )

        primary = choose_primary(markets)

        # -------------------------------------------------
        # OUTPUT
        # -------------------------------------------------

        text = (
            "⚽ GOALLOGIC AI v2.1\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🏟️ {team1_name} vs {team2_name}\n"
            f"Season: {CURRENT_SEASON}\n"
            f"Sample: Last {RECENT_MATCHES} available matches\n\n"
        )

        text += (
            f"🔵 {team1_name}\n"
            f"Form: {form_string(matches1)}\n"
            f"W/D/L: "
            f"{stats1['wins']}/"
            f"{stats1['draws']}/"
            f"{stats1['losses']}\n"
            f"GF/GA: "
            f"{stats1['gf']}/"
            f"{stats1['ga']}\n"
            f"Avg goals: "
            f"{stats1['avg_gf']}/"
            f"{stats1['avg_ga']}\n"
            f"Scoring: {stats1['scoring']}%\n"
            f"Clean sheet: {stats1['clean_sheet']}%\n"
            f"Over 2.5: {stats1['over25']}%\n"
            f"BTTS: {stats1['btts']}%\n\n"
        )

        text += (
            f"🔴 {team2_name}\n"
            f"Form: {form_string(matches2)}\n"
            f"W/D/L: "
            f"{stats2['wins']}/"
            f"{stats2['draws']}/"
            f"{stats2['losses']}\n"
            f"GF/GA: "
            f"{stats2['gf']}/"
            f"{stats2['ga']}\n"
            f"Avg goals: "
            f"{stats2['avg_gf']}/"
            f"{stats2['avg_ga']}\n"
            f"Scoring: {stats2['scoring']}%\n"
            f"Clean sheet: {stats2['clean_sheet']}%\n"
            f"Over 2.5: {stats2['over25']}%\n"
            f"BTTS: {stats2['btts']}%\n\n"
        )

        text += (
            f"🏠 {team1_name} home sample: "
            f"{home1['sample']}\n"
            f"Scoring: {home1['scoring']}%\n"
            f"Clean sheet: {home1['clean_sheet']}%\n"
            f"Over 2.5: {home1['over25']}%\n\n"
        )

        text += (
            f"✈️ {team2_name} away sample: "
            f"{away2['sample']}\n"
            f"Scoring: {away2['scoring']}%\n"
            f"Clean sheet: {away2['clean_sheet']}%\n"
            f"Over 2.5: {away2['over25']}%\n\n"
        )

        text += "📊 MARKET CONFIDENCE\n"

        for market, value in markets.items():

            text += (
                f"• {market}: {value}%\n"
            )

        text += "\n"

        if primary:

            _, market, value = primary

            text += (
                "🎯 PRIMARY SIGNAL\n"
                f"{market} — {value}%\n\n"
            )

        else:

            text += (
                "⚠️ PRIMARY SIGNAL\n"
                "No market reached the confidence threshold.\n\n"
            )

        text += (
            "⚠️ Small samples reduce reliability.\n"
            "Statistics are historical indicators, "
            "not guarantees."
        )

        await update.message.reply_text(text)

    except Exception as e:

        print(
            "ANALYZE ERROR:",
            repr(e)
        )

        await update.message.reply_text(
            "❌ Analysis error:\n\n"
            f"{str(e)[:2500]}"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    if not TELEGRAM_BOT_TOKEN:
        raise Exception(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not OPENFOOT_API_KEY:
        raise Exception(
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

    print(
        "GoalLogic AI bot is starting..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
