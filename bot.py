import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


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
        self.wfile.write(b"GoalLogic AI is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"GoalLogic AI is running on port {port}.")
    server.serve_forever()


threading.Thread(target=start_health_server, daemon=True).start()


# ============================================================
# OPENFOOT API
# ============================================================

def openfoot_get(endpoint, params=None):
    headers = {
        "Authorization": f"Bearer {OPENFOOT_API_KEY}",
        "Accept": "application/json",
    }

    response = requests.get(
        f"{OPENFOOT_BASE}{endpoint}",
        headers=headers,
        params=params or {},
        timeout=20,
    )

    response.raise_for_status()
    return response.json()


# ============================================================
# TEAM SEARCH
# ============================================================

def search_team(team_name):
    data = openfoot_get(
        "/v1/search",
        params={"q": team_name}
    )

    results = data.get("data", [])

    if not results:
        return None

    wanted = team_name.lower().strip()

    for item in results:
        name = str(item.get("name", "")).lower()

        if name == wanted:
            return item

    return results[0]

# ============================================================
# MATCH PARSER
# ============================================================

def parse_match(match, requested_team_id):
    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    home_id = home_team.get("id")
    away_id = away_team.get("id")

    score = match.get("score") or {}

    home_score = score.get("home")
    away_score = score.get("away")

    if home_score is None or away_score is None:
        return None

    kickoff = match.get("kickoffAt")

    if requested_team_id == home_id:
        goals_for = home_score
        goals_against = away_score
        venue = "home"
        opponent = away_team.get("name", "Unknown")

    elif requested_team_id == away_id:
        goals_for = away_score
        goals_against = home_score
        venue = "away"
        opponent = home_team.get("name", "Unknown")

    else:
        return None

    if goals_for > goals_against:
        result = "W"
    elif goals_for == goals_against:
        result = "D"
    else:
        result = "L"

    return {
        "date": kickoff,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "venue": venue,
        "opponent": opponent,
        "result": result,
    }


# ============================================================
# GET RECENT MATCHES
# ============================================================

def get_recent_matches(team_id):
    data = openfoot_get(
        "/v1/matches",
        params={
            "team": team_id,
            "season": CURRENT_SEASON,
            "status": "finished",
        }
    )

    matches = data.get("data", [])

    parsed = []

    for match in matches:
        item = parse_match(match, team_id)

        if item:
            parsed.append(item)

    parsed.sort(
        key=lambda x: x.get("date") or "",
        reverse=True
    )

    return parsed[:RECENT_MATCHES]


# ============================================================
# CALCULATE STATS
# ============================================================

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
            "over_0_5": 0,
            "over_1_5": 0,
            "over_2_5": 0,
            "under_3_5": 0,
            "btts": 0,
        }

    total = len(matches)

    wins = sum(1 for m in matches if m["result"] == "W")
    draws = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")

    gf = sum(m["goals_for"] for m in matches)
    ga = sum(m["goals_against"] for m in matches)

    scoring = sum(
        1 for m in matches
        if m["goals_for"] > 0
    )

    clean_sheet = sum(
        1 for m in matches
        if m["goals_against"] == 0
    )

    over_0_5 = sum(
        1 for m in matches
        if m["goals_for"] + m["goals_against"] > 0
    )

    over_1_5 = sum(
        1 for m in matches
        if m["goals_for"] + m["goals_against"] > 1
    )

    over_2_5 = sum(
        1 for m in matches
        if m["goals_for"] + m["goals_against"] > 2
    )

    under_3_5 = sum(
        1 for m in matches
        if m["goals_for"] + m["goals_against"] < 4
    )

    btts = sum(
        1 for m in matches
        if m["goals_for"] > 0
        and m["goals_against"] > 0
    )

    return {
        "sample": total,

        # IMPORTANT:
        # These keys are now always present.
        "wins": wins,
        "draws": draws,
        "losses": losses,

        "gf": gf,
        "ga": ga,

        "avg_gf": gf / total,
        "avg_ga": ga / total,

        "scoring": scoring / total * 100,
        "clean_sheet": clean_sheet / total * 100,

        "over_0_5": over_0_5 / total * 100,
        "over_1_5": over_1_5 / total * 100,
        "over_2_5": over_2_5 / total * 100,
        "under_3_5": under_3_5 / total * 100,

        "btts": btts / total * 100,
    }


# ============================================================
# VENUE STATS
# ============================================================

def venue_stats(matches, venue):

    selected = [
        m for m in matches
        if m["venue"] == venue
    ]

    return calculate_stats(selected)


# ============================================================
# CONFIDENCE
# ============================================================

def clamp(value, low=MIN_CONFIDENCE, high=MAX_CONFIDENCE):
    return max(low, min(high, value))


def confidence_from_sample(base, sample):

    if sample <= 1:
        return min(base, 55)

    if sample == 2:
        return min(base, 60)

    if sample == 3:
        return min(base, 65)

    if sample == 4:
        return min(base, 70)

    return clamp(base)


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_markets(
    team1_stats,
    team2_stats,
    team1_home,
    team2_away,
):

    markets = {}

    # --------------------------------------------------------
    # 1X
    # --------------------------------------------------------

    one_x = (
        team1_home["wins"]
        + team1_home["draws"]
    ) / max(team1_home["sample"], 1) * 100

    opponent_away_losses = (
        team2_away["losses"]
        / max(team2_away["sample"], 1)
        * 100
    )

    one_x_score = (
        one_x * 0.65
        + opponent_away_losses * 0.35
    )

    markets["1X"] = confidence_from_sample(
        one_x_score,
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # X2
    # --------------------------------------------------------

    x2 = (
        team2_away["wins"]
        + team2_away["draws"]
    ) / max(team2_away["sample"], 1) * 100

    opponent_home_losses = (
        team1_home["losses"]
        / max(team1_home["sample"], 1)
        * 100
    )

    x2_score = (
        x2 * 0.65
        + opponent_home_losses * 0.35
    )

    markets["X2"] = confidence_from_sample(
        x2_score,
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # OVER 0.5
    # --------------------------------------------------------

    over_0_5 = (
        team1_stats["over_0_5"]
        + team2_stats["over_0_5"]
    ) / 2

    markets["O0.5"] = confidence_from_sample(
        over_0_5,
        min(
            team1_stats["sample"],
            team2_stats["sample"]
        )
    )

    # --------------------------------------------------------
    # OVER 1.5
    # --------------------------------------------------------

    over_1_5 = (
        team1_stats["over_1_5"]
        + team2_stats["over_1_5"]
        + team1_home["over_1_5"]
        + team2_away["over_1_5"]
    ) / 4

    markets["O1.5"] = confidence_from_sample(
        over_1_5,
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # OVER 2.5
    # --------------------------------------------------------

    over_2_5 = (
        team1_stats["over_2_5"]
        + team2_stats["over_2_5"]
        + team1_home["over_2_5"]
        + team2_away["over_2_5"]
    ) / 4

    markets["O2.5"] = confidence_from_sample(
        over_2_5,
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # UNDER 3.5
    # --------------------------------------------------------

    under_3_5 = (
        team1_stats["under_3_5"]
        + team2_stats["under_3_5"]
        + team1_home["under_3_5"]
        + team2_away["under_3_5"]
    ) / 4

    markets["U3.5"] = confidence_from_sample(
        under_3_5,
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # BTTS
    # --------------------------------------------------------

    btts = (
        team1_stats["btts"]
        + team2_stats["btts"]
        + team1_home["btts"]
        + team2_away["btts"]
    ) / 4

    markets["BTTS"] = confidence_from_sample(
        btts,
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # TEAM 1 TO SCORE
    # --------------------------------------------------------

    team1_score = (
        team1_stats["scoring"] * 0.45
        + team1_home["scoring"] * 0.35
        + (100 - team2_stats["clean_sheet"]) * 0.20
    )

    # Strong penalty when home scoring sample is poor.
    if team1_home["scoring"] == 0:
        team1_score -= 15

    markets["Team1 score"] = confidence_from_sample(
        team1_score,
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # TEAM 2 TO SCORE
    # --------------------------------------------------------

    team2_score = (
        team2_stats["scoring"] * 0.45
        + team2_away["scoring"] * 0.35
        + (100 - team1_stats["clean_sheet"]) * 0.20
    )

    if team2_away["scoring"] == 0:
        team2_score -= 15

    markets["Team2 score"] = confidence_from_sample(
        team2_score,
        min(
            team1_home["sample"],
            team2_away["sample"]
        )
    )

    # --------------------------------------------------------
    # FINAL CLAMP
    # --------------------------------------------------------

    for market in markets:
        markets[market] = round(
            clamp(markets[market]),
            1
        )

    return markets


# ============================================================
# PRIMARY MARKET
# ============================================================

def choose_primary(markets):

    allowed = {
        market: confidence
        for market, confidence in markets.items()
        if confidence >= 68
    }

    if not allowed:
        return None, None

    # Prefer more meaningful markets over O0.5
    priority = {
        "O2.5": 8,
        "U3.5": 8,
        "BTTS": 8,
        "1X": 7,
        "X2": 7,
        "O1.5": 6,
        "Team1 score": 6,
        "Team2 score": 6,
        "O0.5": 2,
    }

    best_market = max(
        allowed,
        key=lambda m: (
            allowed[m],
            priority.get(m, 0)
        )
    )

    return best_market, allowed[best_market]


# ============================================================
# FORMAT STATS
# ============================================================

def format_stats(name, stats):

    form = (
        "W" * stats["wins"]
        + "D" * stats["draws"]
        + "L" * stats["losses"]
    )

    return (
        f"{name} last {stats['sample']}: "
        f"{form}, "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']}, "
        f"GF{stats['gf']} GA{stats['ga']}, "
        f"avg {stats['avg_gf']:.2f}/{stats['avg_ga']:.2f}, "
        f"O0.5 {stats['over_0_5']:.0f}%, "
        f"O1.5 {stats['over_1_5']:.0f}%, "
        f"O2.5 {stats['over_2_5']:.0f}%, "
        f"U3.5 {stats['under_3_5']:.0f}%, "
        f"BTTS {stats['btts']:.0f}%, "
        f"scoring {stats['scoring']:.0f}%, "
        f"CS {stats['clean_sheet']:.0f}%"
    )


# ============================================================
# /START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = (
        "⚽ Welcome to GoalLogic AI!\n\n"
        "I analyze football matches using historical statistics.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )

    await update.message.reply_text(message)


# ============================================================
# /TEAM
# ============================================================

async def team_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text(
            "Use:\n/team Chelsea"
        )
        return

    name = " ".join(context.args)

    try:
        team = search_team(name)

        if not team:
            await update.message.reply_text(
                f"❌ Team not found: {name}"
            )
            return

        await update.message.reply_text(
            "⚽ TEAM SEARCH\n\n"
            f"Team: {team.get('name', name)}\n"
            f"ID: {team.get('id', 'Unknown')}\n"
            f"Country: {team.get('country', 'Unknown')}"
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Team search error:\n{e}"
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
            "Use:\n/fixtures Sunderland"
        )
        return

    name = " ".join(context.args)

    try:
        team = search_team(name)

        if not team:
            await update.message.reply_text(
                f"❌ Team not found: {name}"
            )
            return

        team_id = team.get("id")

        data = openfoot_get(
            "/v1/matches",
            params={
                "team": team_id,
                "season": CURRENT_SEASON,
            }
        )

        matches = data.get("data", [])

        if not matches:
            await update.message.reply_text(
                f"❌ No fixtures found for {name}."
            )
            return

        matches.sort(
            key=lambda m: m.get("kickoffAt") or ""
        )

        lines = [
            f"📅 {team.get('name', name)} — {CURRENT_SEASON}\n"
        ]

        for match in matches[:10]:

            home = (match.get("homeTeam") or {}).get(
                "name",
                "Unknown"
            )

            away = (match.get("awayTeam") or {}).get(
                "name",
                "Unknown"
            )

            kickoff = match.get(
                "kickoffAt",
                "Unknown"
            )

            lines.append(
                f"• {kickoff}\n"
                f"  {home} vs {away}"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Fixtures error:\n{e}"
        )


# ============================================================
# /ANALYZE
# ============================================================

async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:
        await update.message.reply_text(
            "Use:\n/analyze Sunderland vs Arsenal"
        )
        return

    query = " ".join(context.args)

    parts = query.split(" vs ")

    if len(parts) != 2:
        parts = query.lower().split(" vs ")

    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Use this format:\n"
            "/analyze Sunderland vs Arsenal"
        )
        return

    team1_name = parts[0].strip()
    team2_name = parts[1].strip()

    try:

        team1 = search_team(team1_name)
        team2 = search_team(team2_name)

        if not team1:
            await update.message.reply_text(
                f"❌ Team not found: {team1_name}"
            )
            return

        if not team2:
            await update.message.reply_text(
                f"❌ Team not found: {team2_name}"
            )
            return

        team1_id = team1.get("id")
        team2_id = team2.get("id")

        team1_matches = get_recent_matches(team1_id)
        team2_matches = get_recent_matches(team2_id)

        if not team1_matches:
            await update.message.reply_text(
                f"❌ No completed matches found for "
                f"{team1.get('name', team1_name)}."
            )
            return

        if not team2_matches:
            await update.message.reply_text(
                f"❌ No completed matches found for "
                f"{team2.get('name', team2_name)}."
            )
            return

        team1_stats = calculate_stats(team1_matches)
        team2_stats = calculate_stats(team2_matches)

        team1_home = venue_stats(
            team1_matches,
            "home"
        )

        team2_away = venue_stats(
            team2_matches,
            "away"
        )

        markets = analyze_markets(
            team1_stats,
            team2_stats,
            team1_home,
            team2_away
        )

        primary_market, primary_confidence = choose_primary(
            markets
        )

        message = (
            "⚽ GOALLOGIC AI — MATCH ANALYSIS\n\n"
            f"🏟️ {team1.get('name', team1_name)} "
            f"vs "
            f"{team2.get('name', team2_name)}\n\n"
            f"Season: {CURRENT_SEASON}\n"
            f"Sample: Last {RECENT_MATCHES} available matches\n\n"
        )

        message += format_stats(
            team1.get("name", team1_name),
            team1_stats
        )

        message += "\n\n"

        message += format_stats(
            team2.get("name", team2_name),
            team2_stats
        )

        message += "\n\n"

        message += (
            f"{team1.get('name', team1_name)} HOME sample "
            f"{team1_home['sample']}: "
            f"W/D/L "
            f"{team1_home['wins']}/"
            f"{team1_home['draws']}/"
            f"{team1_home['losses']}, "
            f"scoring {team1_home['scoring']:.0f}%, "
            f"CS {team1_home['clean_sheet']:.0f}%, "
            f"O2.5 {team1_home['over_2_5']:.0f}%, "
            f"BTTS {team1_home['btts']:.0f}%"
        )

        message += "\n"

        message += (
            f"{team2.get('name', team2_name)} AWAY sample "
            f"{team2_away['sample']}: "
            f"W/D/L "
            f"{team2_away['wins']}/"
            f"{team2_away['draws']}/"
            f"{team2_away['losses']}, "
            f"scoring {team2_away['scoring']:.0f}%, "
            f"CS {team2_away['clean_sheet']:.0f}%, "
            f"O2.5 {team2_away['over_2_5']:.0f}%, "
            f"BTTS {team2_away['btts']:.0f}%"
        )

        message += "\n\n📊 MARKETS\n"

        for market, confidence in markets.items():
            message += (
                f"{market}: {confidence:.0f}%\n"
            )

        message += "\n"

        if primary_market:
            message += (
                f"🎯 PRIMARY SIGNAL\n"
                f"{primary_market} — "
                f"{primary_confidence:.0f}%\n\n"
                "⚠️ Statistical signal only — "
                "not a guarantee."
            )
        else:
            message += (
                "⚠️ NO STRONG PRIMARY SIGNAL\n\n"
                "The available sample does not provide "
                "enough evidence for a high-confidence market."
            )

        await update.message.reply_text(message)

    except Exception as e:

        print(
            f"ANALYSIS ERROR: {type(e).__name__}: {e}"
        )

        await update.message.reply_text(
            f"❌ Analysis error:\n{e}"
        )


# ============================================================
# /APITEST
# ============================================================

async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        openfoot_get(
            "/v1/teams",
            params={"search": "Chelsea"}
        )

        await update.message.reply_text(
            "🔧 OPENFOOT TEST PASSED\n\n"
            "OpenFoot API is connected successfully."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ OPENFOOT TEST FAILED\n\n{e}"
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

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("team", team_command)
    )

    app.add_handler(
        CommandHandler("fixtures", fixtures_command)
    )

    app.add_handler(
        CommandHandler("analyze", analyze_command)
    )

    app.add_handler(
        CommandHandler("apitest", apitest_command)
    )

    print("GoalLogic AI Telegram bot is starting...")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
