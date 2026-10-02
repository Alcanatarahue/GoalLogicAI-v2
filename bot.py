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
# RENDER HEALTH SERVER
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
    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"GoalLogic AI is running on port {port}.")
    server.serve_forever()


threading.Thread(
    target=start_health_server,
    daemon=True
).start()


# ============================================================
# OPENFOOT
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
# IMPORTANT: /v1/search?q=...
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

        name = str(
            item.get("name", "")
        ).lower().strip()

        if name == wanted:
            return item

    return results[0]


# ============================================================
# PARSE MATCH
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

    if requested_team_id == home_id:

        goals_for = home_score
        goals_against = away_score
        venue = "home"
        opponent = away_team.get(
            "name",
            "Unknown"
        )

    elif requested_team_id == away_id:

        goals_for = away_score
        goals_against = home_score
        venue = "away"
        opponent = home_team.get(
            "name",
            "Unknown"
        )

    else:
        return None

    if goals_for > goals_against:
        result = "W"

    elif goals_for == goals_against:
        result = "D"

    else:
        result = "L"

    return {
        "date": match.get("kickoffAt"),
        "goals_for": goals_for,
        "goals_against": goals_against,
        "venue": venue,
        "opponent": opponent,
        "result": result,
    }


# ============================================================
# RECENT MATCHES
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

        item = parse_match(
            match,
            team_id
        )

        if item:
            parsed.append(item)

    parsed.sort(
        key=lambda x: x.get("date") or "",
        reverse=True
    )

    return parsed[:RECENT_MATCHES]


# ============================================================
# STATISTICS
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
            "over_1_5": 0,
            "over_2_5": 0,
            "under_3_5": 0,
            "btts": 0,
        }

    total = len(matches)

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
        m["goals_for"]
        for m in matches
    )

    ga = sum(
        m["goals_against"]
        for m in matches
    )

    scoring = sum(
        1 for m in matches
        if m["goals_for"] > 0
    )

    clean_sheet = sum(
        1 for m in matches
        if m["goals_against"] == 0
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
        if (
            m["goals_for"] > 0
            and m["goals_against"] > 0
        )
    )

    return {
        "sample": total,

        "wins": wins,
        "draws": draws,
        "losses": losses,

        "gf": gf,
        "ga": ga,

        "avg_gf": gf / total,
        "avg_ga": ga / total,

        "scoring": scoring / total * 100,

        "clean_sheet": (
            clean_sheet / total * 100
        ),

        "over_1_5": (
            over_1_5 / total * 100
        ),

        "over_2_5": (
            over_2_5 / total * 100
        ),

        "under_3_5": (
            under_3_5 / total * 100
        ),

        "btts": (
            btts / total * 100
        ),
    }


# ============================================================
# HOME / AWAY
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

def clamp(value):

    return max(
        MIN_CONFIDENCE,
        min(MAX_CONFIDENCE, value)
    )


def sample_reliability(sample):

    if sample <= 1:
        return 0.25

    if sample == 2:
        return 0.40

    if sample == 3:
        return 0.55

    if sample == 4:
        return 0.70

    return 0.85


def conservative_confidence(
    raw_score,
    sample
):

    reliability = sample_reliability(
        sample
    )

    # Pull small samples toward 50%.
    score = (
        50
        + (raw_score - 50)
        * reliability
    )

    return round(
        clamp(score),
        1
    )


# ============================================================
# MARKET ENGINE
# ============================================================

def analyze_markets(
    team1_stats,
    team2_stats,
    team1_home,
    team2_away,
):

    markets = {}

    sample = min(
        team1_home["sample"],
        team2_away["sample"]
    )

    # --------------------------------------------------------
    # 1X
    # --------------------------------------------------------

    team1_home_safe = (
        (
            team1_home["wins"]
            + team1_home["draws"]
        )
        / max(team1_home["sample"], 1)
        * 100
    )

    team2_away_losses = (
        team2_away["losses"]
        / max(team2_away["sample"], 1)
        * 100
    )

    raw_1x = (
        team1_home_safe * 0.65
        + team2_away_losses * 0.35
    )

    markets["1X"] = conservative_confidence(
        raw_1x,
        sample
    )

    # --------------------------------------------------------
    # X2
    # --------------------------------------------------------

    team2_away_safe = (
        (
            team2_away["wins"]
            + team2_away["draws"]
        )
        / max(team2_away["sample"], 1)
        * 100
    )

    team1_home_losses = (
        team1_home["losses"]
        / max(team1_home["sample"], 1)
        * 100
    )

    raw_x2 = (
        team2_away_safe * 0.65
        + team1_home_losses * 0.35
    )

    markets["X2"] = conservative_confidence(
        raw_x2,
        sample
    )

    # --------------------------------------------------------
    # OVER 1.5
    # --------------------------------------------------------

    raw_o15 = (
        team1_stats["over_1_5"] * 0.25
        + team2_stats["over_1_5"] * 0.25
        + team1_home["over_1_5"] * 0.25
        + team2_away["over_1_5"] * 0.25
    )

    markets["O1.5"] = conservative_confidence(
        raw_o15,
        sample
    )

    # --------------------------------------------------------
    # OVER 2.5
    # --------------------------------------------------------

    raw_o25 = (
        team1_stats["over_2_5"] * 0.20
        + team2_stats["over_2_5"] * 0.20
        + team1_home["over_2_5"] * 0.30
        + team2_away["over_2_5"] * 0.30
    )

    markets["O2.5"] = conservative_confidence(
        raw_o25,
        sample
    )

    # --------------------------------------------------------
    # UNDER 3.5
    # --------------------------------------------------------

    raw_u35 = (
        team1_stats["under_3_5"] * 0.20
        + team2_stats["under_3_5"] * 0.20
        + team1_home["under_3_5"] * 0.30
        + team2_away["under_3_5"] * 0.30
    )

    markets["U3.5"] = conservative_confidence(
        raw_u35,
        sample
    )

    # --------------------------------------------------------
    # BTTS
    # --------------------------------------------------------

    raw_btts = (
        team1_stats["btts"] * 0.20
        + team2_stats["btts"] * 0.20
        + team1_home["btts"] * 0.30
        + team2_away["btts"] * 0.30
    )

    markets["BTTS"] = conservative_confidence(
        raw_btts,
        sample
    )

    # --------------------------------------------------------
    # TEAM 1 TO SCORE
    # --------------------------------------------------------

    raw_team1_score = (
        team1_stats["scoring"] * 0.30
        + team1_home["scoring"] * 0.45
        + (
            100 - team2_away["clean_sheet"]
        ) * 0.25
    )

    # Strong venue warning.
    if team1_home["scoring"] <= 50:
        raw_team1_score -= 12

    if team2_away["clean_sheet"] >= 67:
        raw_team1_score -= 8

    markets["Team1 score"] = conservative_confidence(
        raw_team1_score,
        sample
    )

    # --------------------------------------------------------
    # TEAM 2 TO SCORE
    # --------------------------------------------------------

    raw_team2_score = (
        team2_stats["scoring"] * 0.30
        + team2_away["scoring"] * 0.45
        + (
            100 - team1_home["clean_sheet"]
        ) * 0.25
    )

    if team2_away["scoring"] <= 50:
        raw_team2_score -= 12

    if team1_home["clean_sheet"] >= 67:
        raw_team2_score -= 8

    markets["Team2 score"] = conservative_confidence(
        raw_team2_score,
        sample
    )

    return markets


# ============================================================
# PRIMARY SIGNAL
# ============================================================

def choose_primary(markets):

    # O0.5 deliberately removed.
    # We only promote meaningful markets.

    priority = {
        "1X": 9,
        "X2": 9,
        "O1.5": 8,
        "U3.5": 8,
        "O2.5": 7,
        "BTTS": 7,
        "Team1 score": 6,
        "Team2 score": 6,
    }

    candidates = []

    for market, confidence in markets.items():

        if confidence >= 68:

            candidates.append(
                (
                    confidence,
                    priority.get(market, 0),
                    market
                )
            )

    if not candidates:
        return None, None

    candidates.sort(
        reverse=True
    )

    confidence, _, market = candidates[0]

    return market, confidence


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
        f"W/D/L "
        f"{stats['wins']}/"
        f"{stats['draws']}/"
        f"{stats['losses']}, "
        f"GF{stats['gf']} "
        f"GA{stats['ga']}, "
        f"avg "
        f"{stats['avg_gf']:.2f}/"
        f"{stats['avg_ga']:.2f}, "
        f"O1.5 "
        f"{stats['over_1_5']:.0f}%, "
        f"O2.5 "
        f"{stats['over_2_5']:.0f}%, "
        f"U3.5 "
        f"{stats['under_3_5']:.0f}%, "
        f"BTTS "
        f"{stats['btts']:.0f}%, "
        f"scoring "
        f"{stats['scoring']:.0f}%, "
        f"CS "
        f"{stats['clean_sheet']:.0f}%"
    )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "⚽ Welcome to GoalLogic AI!\n\n"
        "I analyze football matches using "
        "historical statistics.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
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
            "Use:\n/team Sunderland"
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
            f"📅 {team.get('name', name)} "
            f"— {CURRENT_SEASON}\n"
        ]

        for match in matches[:10]:

            home = (
                match.get("homeTeam") or {}
            ).get(
                "name",
                "Unknown"
            )

            away = (
                match.get("awayTeam") or {}
            ).get(
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
            "Use:\n"
            "/analyze Sunderland vs Arsenal"
        )

        return

    query = " ".join(context.args)

    parts = query.split(" vs ")

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

        team1_matches = get_recent_matches(
            team1_id
        )

        team2_matches = get_recent_matches(
            team2_id
        )

        if not team1_matches:

            await update.message.reply_text(
                f"❌ No completed matches found "
                f"for {team1.get('name', team1_name)}."
            )

            return

        if not team2_matches:

            await update.message.reply_text(
                f"❌ No completed matches found "
                f"for {team2.get('name', team2_name)}."
            )

            return

        team1_stats = calculate_stats(
            team1_matches
        )

        team2_stats = calculate_stats(
            team2_matches
        )

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

        primary_market, primary_confidence = (
            choose_primary(markets)
        )

        team1_display = team1.get(
            "name",
            team1_name
        )

        team2_display = team2.get(
            "name",
            team2_name
        )

        message = (
            "⚽ GOALLOGIC AI v2.1\n\n"
            f"🏟️ {team1_display} "
            f"vs "
            f"{team2_display}\n\n"
            f"Season: {CURRENT_SEASON}\n"
            f"Sample: Last {RECENT_MATCHES} "
            f"available matches\n\n"
        )

        message += format_stats(
            team1_display,
            team1_stats
        )

        message += "\n\n"

        message += format_stats(
            team2_display,
            team2_stats
        )

        message += "\n\n"

        message += (
            f"{team1_display} HOME sample "
            f"{team1_home['sample']}: "
            f"W/D/L "
            f"{team1_home['wins']}/"
            f"{team1_home['draws']}/"
            f"{team1_home['losses']}, "
            f"scoring "
            f"{team1_home['scoring']:.0f}%, "
            f"CS "
            f"{team1_home['clean_sheet']:.0f}%, "
            f"O2.5 "
            f"{team1_home['over_2_5']:.0f}%, "
            f"BTTS "
            f"{team1_home['btts']:.0f}%"
        )

        message += "\n"

        message += (
            f"{team2_display} AWAY sample "
            f"{team2_away['sample']}: "
            f"W/D/L "
            f"{team2_away['wins']}/"
            f"{team2_away['draws']}/"
            f"{team2_away['losses']}, "
            f"scoring "
            f"{team2_away['scoring']:.0f}%, "
            f"CS "
            f"{team2_away['clean_sheet']:.0f}%, "
            f"O2.5 "
            f"{team2_away['over_2_5']:.0f}%, "
            f"BTTS "
            f"{team2_away['btts']:.0f}%"
        )

        message += "\n\n📊 MARKETS\n"

        for market, confidence in markets.items():

            if confidence >= 68:
                marker = "🟢"

            elif confidence <= 55:
                marker = "🔴"

            else:
                marker = "🟡"

            message += (
                f"{marker} {market}: "
                f"{confidence:.0f}%\n"
            )

        message += "\n"

        if primary_market:

            message += (
                "🎯 PRIMARY SIGNAL\n"
                f"{primary_market} — "
                f"{primary_confidence:.0f}%\n\n"
                "📌 Advice: BET only if the "
                "available odds justify the risk.\n\n"
                "⚠️ Statistical signal only — "
                "not a guarantee."
            )

        else:

            message += (
                "⚠️ NO STRONG PRIMARY SIGNAL\n\n"
                "The available sample does not "
                "provide enough evidence for a "
                "high-confidence market.\n\n"
                "📌 Advice: AVOID."
            )

        await update.message.reply_text(
            message
        )

    except Exception as e:

        print(
            f"ANALYSIS ERROR: "
            f"{type(e).__name__}: {e}"
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
            "/v1/search",
            params={"q": "Chelsea"}
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

    print(
        "GoalLogic AI v2.1 Telegram bot is starting..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
