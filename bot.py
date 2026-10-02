import os
import json
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
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GoalLogic AI v2.1 is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"GoalLogic AI v2.1 health server running on port {port}")
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

    url = OPENFOOT_BASE + endpoint

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=20,
    )

    print("OPENFOOT STATUS:", response.status_code)
    print("OPENFOOT URL:", response.url)

    response.raise_for_status()

    return response.json()


# =========================================================
# BASIC HELPERS
# =========================================================

def normalize_name(name):

    if not name:
        return ""

    return (
        str(name)
        .lower()
        .replace("-", " ")
        .replace("_", " ")
        .strip()
    )


def safe_percentage(value):

    try:
        return round(float(value))
    except Exception:
        return 0


# =========================================================
# TEAM SEARCH
# =========================================================

def search_team(team_name):

    data = openfoot_get(
        "/v1/search",
        params={"q": team_name},
    )

    print("SEARCH RESPONSE:")
    print(json.dumps(data, indent=2)[:5000])

    results = []

    if isinstance(data, list):
        results = data

    elif isinstance(data, dict):

        for key in ["data", "results", "teams", "items"]:

            if isinstance(data.get(key), list):
                results = data[key]
                break

    if not results:
        return None

    wanted = normalize_name(team_name)

    # Exact name first
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
            or team.get("display_name")
            or ""
        )

        if wanted in normalize_name(name):
            return team

    return results[0]


def team_id_from_result(team):

    if not team:
        return None

    return (
        team.get("id")
        or team.get("team_id")
        or team.get("teamId")
    )


def team_name_from_result(team):

    if not team:
        return "Unknown"

    return (
        team.get("name")
        or team.get("team_name")
        or team.get("display_name")
        or "Unknown"
    )


# =========================================================
# SCORE PARSER
# =========================================================

def get_score(match):

    # Standard structure
    score = match.get("score")

    if isinstance(score, dict):

        fulltime = score.get("fulltime")

        if isinstance(fulltime, dict):

            home = fulltime.get("home")
            away = fulltime.get("away")

            if isinstance(home, (int, float)) and isinstance(
                away, (int, float)
            ):
                return int(home), int(away)

        # Alternative score structures
        home = score.get("home")
        away = score.get("away")

        if isinstance(home, (int, float)) and isinstance(
            away, (int, float)
        ):
            return int(home), int(away)

    # Other possible structures
    for key in ["goals", "result", "scores"]:

        obj = match.get(key)

        if isinstance(obj, dict):

            home = obj.get("home")
            away = obj.get("away")

            if isinstance(home, (int, float)) and isinstance(
                away, (int, float)
            ):
                return int(home), int(away)

    # Direct fields
    home = match.get("home_score")
    away = match.get("away_score")

    if isinstance(home, (int, float)) and isinstance(
        away, (int, float)
    ):
        return int(home), int(away)

    return None


# =========================================================
# TEAM SIDE
# =========================================================

def team_side(match, team_id):

    teams = match.get("teams")

    if isinstance(teams, dict):

        home = teams.get("home")
        away = teams.get("away")

        if isinstance(home, dict):

            home_id = (
                home.get("id")
                or home.get("team_id")
                or home.get("teamId")
            )

            if str(home_id) == str(team_id):
                return "home"

        if isinstance(away, dict):

            away_id = (
                away.get("id")
                or away.get("team_id")
                or away.get("teamId")
            )

            if str(away_id) == str(team_id):
                return "away"

    # Alternative structures
    home_id = (
        match.get("home_team_id")
        or match.get("homeTeamId")
    )

    away_id = (
        match.get("away_team_id")
        or match.get("awayTeamId")
    )

    if str(home_id) == str(team_id):
        return "home"

    if str(away_id) == str(team_id):
        return "away"

    return None


# =========================================================
# MATCH LIST EXTRACTION
# =========================================================

def extract_matches(data):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in [
        "data",
        "matches",
        "fixtures",
        "results",
        "items",
    ]:

        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


# =========================================================
# RECENT MATCHES
# =========================================================

def get_recent_matches(team_id):

    data = openfoot_get(
        "/v1/matches",
        params={
            "team": team_id,
            "season": CURRENT_SEASON,
            "status": "finished",
        },
    )

    matches = extract_matches(data)

    print("MATCH COUNT FROM API:", len(matches))

    parsed = []

    for match in matches:

        score = get_score(match)

        if not score:
            continue

        side = team_side(match, team_id)

        if side == "home":

            gf = score[0]
            ga = score[1]

        elif side == "away":

            gf = score[1]
            ga = score[0]

        else:
            continue

        parsed.append({
            "gf": gf,
            "ga": ga,
            "side": side,
            "raw": match,
        })

    return parsed[:RECENT_MATCHES]


# =========================================================
# VENUE STATS
# =========================================================

def venue_stats(matches, venue):

    selected = [
        m for m in matches
        if m["side"] == venue
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

        "scoring": safe_percentage(
            sum(m["gf"] > 0 for m in selected)
            / len(selected)
            * 100
        ),

        "clean_sheet": safe_percentage(
            sum(m["ga"] == 0 for m in selected)
            / len(selected)
            * 100
        ),

        "over25": safe_percentage(
            sum(
                m["gf"] + m["ga"] > 2
                for m in selected
            )
            / len(selected)
            * 100
        ),

        "btts": safe_percentage(
            sum(
                m["gf"] > 0 and m["ga"] > 0
                for m in selected
            )
            / len(selected)
            * 100
        ),
    }


# =========================================================
# GENERAL STATS
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
            "btts": 0,
        }

    wins = sum(m["gf"] > m["ga"] for m in matches)
    draws = sum(m["gf"] == m["ga"] for m in matches)
    losses = sum(m["gf"] < m["ga"] for m in matches)

    gf = sum(m["gf"] for m in matches)
    ga = sum(m["ga"] for m in matches)

    return {
        "sample": len(matches),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf,
        "ga": ga,
        "avg_gf": round(gf / len(matches), 2),
        "avg_ga": round(ga / len(matches), 2),

        "scoring": safe_percentage(
            sum(m["gf"] > 0 for m in matches)
            / len(matches)
            * 100
        ),

        "clean_sheet": safe_percentage(
            sum(m["ga"] == 0 for m in matches)
            / len(matches)
            * 100
        ),

        "over25": safe_percentage(
            sum(
                m["gf"] + m["ga"] > 2
                for m in matches
            )
            / len(matches)
            * 100
        ),

        "btts": safe_percentage(
            sum(
                m["gf"] > 0 and m["ga"] > 0
                for m in matches
            )
            / len(matches)
            * 100
        ),
    }


# =========================================================
# DEBUG COMMAND
# =========================================================

async def debug_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "🔎 DEBUG\n\n"
            "Use:\n"
            "/debug Sunderland\n\n"
            "This will show the raw OpenFoot match structure."
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

        team_id = team_id_from_result(team)

        data = openfoot_get(
            "/v1/matches",
            params={
                "team": team_id,
                "season": CURRENT_SEASON,
                "status": "finished",
            },
        )

        matches = extract_matches(data)

        if not matches:

            text = (
                "🔎 OPENFOOT DEBUG\n\n"
                f"Team: {team_name}\n"
                f"Team ID: {team_id}\n\n"
                "No match list was found.\n\n"
                "TOP LEVEL RESPONSE:\n"
                f"{json.dumps(data, indent=2)[:3500]}"
            )

            await update.message.reply_text(text)

            return

        first = matches[0]

        text = (
            "🔎 OPENFOOT DEBUG\n\n"
            f"Team: {team_name}\n"
            f"Team ID: {team_id}\n"
            f"Matches returned: {len(matches)}\n\n"
            "FIRST MATCH RAW DATA:\n\n"
            f"{json.dumps(first, indent=2)[:3500]}"
        )

        await update.message.reply_text(text)

    except Exception as e:

        await update.message.reply_text(
            "❌ DEBUG ERROR\n\n"
            f"{str(e)[:3000]}"
        )


# =========================================================
# TEAM COMMAND
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

        team_id = team_id_from_result(team)
        team_name = team_name_from_result(team)

        country = (
            team.get("country")
            or team.get("country_name")
            or "Unknown"
        )

        if isinstance(country, dict):
            country = country.get("name", "Unknown")

        await update.message.reply_text(
            "⚽ TEAM SEARCH\n\n"
            f"Team: {team_name}\n"
            f"ID: {team_id}\n"
            f"Country: {country}"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error:\n{str(e)[:2500]}"
        )


# =========================================================
# FIXTURES COMMAND
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

        team_id = team_id_from_result(team)
        team_name = team_name_from_result(team)

        data = openfoot_get(
            "/v1/matches",
            params={
                "team": team_id,
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
            "",
        ]

        for match in matches[:10]:

            date = (
                match.get("date")
                or match.get("fixture_date")
                or match.get("match_date")
                or match.get("datetime")
                or "Unknown date"
            )

            teams = match.get("teams")

            home_name = "Home"
            away_name = "Away"

            if isinstance(teams, dict):

                home = teams.get("home")
                away = teams.get("away")

                if isinstance(home, dict):
                    home_name = (
                        home.get("name")
                        or home.get("team_name")
                        or "Home"
                    )

                if isinstance(away, dict):
                    away_name = (
                        away.get("name")
                        or away.get("team_name")
                        or "Away"
                    )

            lines.append(
                f"• {date}\n"
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
# API TEST
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
# ANALYZE COMMAND
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

    if " vs " not in query.lower():

        await update.message.reply_text(
            "Please use:\n"
            "/analyze Sunderland vs Arsenal"
        )

        return

    parts = query.split(" vs ")

    if len(parts) != 2:

        parts = query.split(" VS ")

    if len(parts) != 2:

        await update.message.reply_text(
            "Please use:\n"
            "/analyze Sunderland vs Arsenal"
        )

        return

    team1_name = parts[0].strip()
    team2_name = parts[1].strip()

    try:

        team1 = search_team(team1_name)
        team2 = search_team(team2_name)

        if not team1 or not team2:

            await update.message.reply_text(
                "❌ Could not find both teams."
            )

            return

        team1_id = team_id_from_result(team1)
        team2_id = team_id_from_result(team2)

        team1_real = team_name_from_result(team1)
        team2_real = team_name_from_result(team2)

        team1_matches = get_recent_matches(team1_id)
        team2_matches = get_recent_matches(team2_id)

        if not team1_matches:

            await update.message.reply_text(
                f"❌ Not enough recent data for {team1_real}.\n\n"
                "The API returned match records, but "
                "their structure needs to be checked.\n\n"
                "Run:\n"
                f"/debug {team1_real}"
            )

            return

        if not team2_matches:

            await update.message.reply_text(
                f"❌ Not enough recent data for {team2_real}.\n\n"
                "Run:\n"
                f"/debug {team2_real}"
            )

            return

        s1 = calculate_stats(team1_matches)
        s2 = calculate_stats(team2_matches)

        home1 = venue_stats(team1_matches, "home")
        away2 = venue_stats(team2_matches, "away")

        total_goals = (
            s1["avg_gf"]
            + s1["avg_ga"]
            + s2["avg_gf"]
            + s2["avg_ga"]
        ) / 2

        markets = {}

        # -------------------------------------------------
        # 1X
        # -------------------------------------------------

        base_1x = (
            50
            + (s1["wins"] / s1["sample"]) * 20
            + (s2["losses"] / s2["sample"]) * 10
        )

        markets["1X"] = min(
            MAX_CONFIDENCE,
            safe_percentage(base_1x),
        )

        # -------------------------------------------------
        # X2
        # -------------------------------------------------

        base_x2 = (
            50
            + (s2["wins"] / s2["sample"]) * 20
            + (s1["losses"] / s1["sample"]) * 10
        )

        markets["X2"] = min(
            MAX_CONFIDENCE,
            safe_percentage(base_x2),
        )

        # -------------------------------------------------
        # OVER 0.5
        # -------------------------------------------------

        base_o05 = (
            50
            + ((s1["scoring"] + s2["scoring"]) / 2 - 50)
            * 0.30
        )

        if total_goals >= 2.5:
            base_o05 += 8

        markets["O0.5"] = min(
            MAX_CONFIDENCE,
            max(MIN_CONFIDENCE, safe_percentage(base_o05)),
        )

        # -------------------------------------------------
        # OVER 1.5
        # -------------------------------------------------

        base_o15 = (
            50
            + ((s1["over25"] + s2["over25"]) / 2 - 50)
            * 0.30
        )

        if total_goals >= 2.5:
            base_o15 += 7

        markets["O1.5"] = min(
            MAX_CONFIDENCE,
            max(MIN_CONFIDENCE, safe_percentage(base_o15)),
        )

        # -------------------------------------------------
        # OVER 2.5
        # -------------------------------------------------

        base_o25 = (
            50
            + ((s1["over25"] + s2["over25"]) / 2 - 50)
            * 0.45
        )

        markets["O2.5"] = min(
            MAX_CONFIDENCE,
            max(MIN_CONFIDENCE, safe_percentage(base_o25)),
        )

        # -------------------------------------------------
        # UNDER 3.5
        # -------------------------------------------------

        under_signal = 100 - (
            s1["over25"] + s2["over25"]
        ) / 2

        base_u35 = 50 + (
            under_signal - 50
        ) * 0.35

        markets["U3.5"] = min(
            MAX_CONFIDENCE,
            max(MIN_CONFIDENCE, safe_percentage(base_u35)),
        )

        # -------------------------------------------------
        # BTTS
        # -------------------------------------------------

        base_btts = (
            50
            + ((s1["btts"] + s2["btts"]) / 2 - 50)
            * 0.40
        )

        markets["BTTS"] = min(
            MAX_CONFIDENCE,
            max(MIN_CONFIDENCE, safe_percentage(base_btts)),
        )

        # -------------------------------------------------
        # TEAM TO SCORE
        # -------------------------------------------------

        team1_score = (
            s1["scoring"] * 0.55
            + (100 - away2["clean_sheet"]) * 0.45
        )

        team2_score = (
            s2["scoring"] * 0.55
            + (100 - home1["clean_sheet"]) * 0.45
        )

        markets[f"{team1_real} score"] = min(
            MAX_CONFIDENCE,
            max(MIN_CONFIDENCE, safe_percentage(team1_score)),
        )

        markets[f"{team2_real} score"] = min(
            MAX_CONFIDENCE,
            max(MIN_CONFIDENCE, safe_percentage(team2_score)),
        )

        # -------------------------------------------------
        # OUTPUT
        # -------------------------------------------------

        text = (
            "⚽ GOALLOGIC AI v2.1\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🏟️ {team1_real} vs {team2_real}\n"
            f"Season: {CURRENT_SEASON}\n"
            f"Sample: Last {RECENT_MATCHES} available matches\n\n"
        )

        text += (
            f"🔵 {team1_real}\n"
            f"Form: {s1['wins']}W "
            f"{s1['draws']}D "
            f"{s1['losses']}L\n"
            f"GF/GA: {s1['gf']}/{s1['ga']}\n"
            f"Avg goals: {s1['avg_gf']}/{s1['avg_ga']}\n"
            f"Scoring: {s1['scoring']}%\n"
            f"Clean sheets: {s1['clean_sheet']}%\n"
            f"Over 2.5: {s1['over25']}%\n"
            f"BTTS: {s1['btts']}%\n\n"
        )

        text += (
            f"🔴 {team2_real}\n"
            f"Form: {s2['wins']}W "
            f"{s2['draws']}D "
            f"{s2['losses']}L\n"
            f"GF/GA: {s2['gf']}/{s2['ga']}\n"
            f"Avg goals: {s2['avg_gf']}/{s2['avg_ga']}\n"
            f"Scoring: {s2['scoring']}%\n"
            f"Clean sheets: {s2['clean_sheet']}%\n"
            f"Over 2.5: {s2['over25']}%\n"
            f"BTTS: {s2['btts']}%\n\n"
        )

        text += (
            f"🏠 {team1_real} home sample: "
            f"{home1['sample']}\n"
            f"Scoring: {home1['scoring']}% | "
            f"Clean sheet: {home1['clean_sheet']}%\n\n"
        )

        text += (
            f"✈️ {team2_real} away sample: "
            f"{away2['sample']}\n"
            f"Scoring: {away2['scoring']}% | "
            f"Clean sheet: {away2['clean_sheet']}%\n\n"
        )

        text += "📊 MARKET CONFIDENCE\n"

        for market, confidence in markets.items():

            text += (
                f"• {market}: "
                f"{confidence}%\n"
            )

        text += (
            "\n⚠️ Small samples reduce reliability.\n"
            "This is statistical analysis, not a guarantee."
        )

        await update.message.reply_text(text)

    except Exception as e:

        print("ANALYZE ERROR:", repr(e))

        await update.message.reply_text(
            "❌ Analysis error:\n\n"
            f"{str(e)[:2500]}"
        )


# =========================================================
# START
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
        "/debug Sunderland\n"
        "/apitest"
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
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
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

    application.add_handler(
        CommandHandler("debug", debug_command)
    )

    application.add_handler(
        CommandHandler("apitest", apitest_command)
    )

    print("GoalLogic AI v2.1 bot is starting...")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
