import os
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
# GOALLOGIC AI v2.2
# Evidence Agreement Upgrade
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY")

OPENFOOT_BASE = "https://openfootapi.com"

CURRENT_SEASON = "2026/27"
RECENT_MATCHES = 5

MIN_CONFIDENCE = 50
MAX_CONFIDENCE = 85

PORT = int(os.getenv("PORT", "10000"))


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GoalLogic AI v2.2 is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


threading.Thread(
    target=start_health_server,
    daemon=True
).start()


print(f"GoalLogic AI v2.2 is running on port {PORT}.")


# ============================================================
# OPENFOOT API
# ============================================================

def openfoot_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        raise Exception("OPENFOOT_API_KEY is missing.")

    url = f"{OPENFOOT_BASE}{endpoint}"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {OPENFOOT_API_KEY}",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=20,
    )

    if not response.ok:
        try:
            error_data = response.json()
            error_message = (
                error_data.get("error", {}).get("message")
                or error_data.get("message")
                or f"HTTP {response.status_code}"
            )
        except Exception:
            error_message = f"HTTP {response.status_code}"

        raise Exception(error_message)

    return response.json()


# ============================================================
# TEAM SEARCH
# ============================================================

def search_team(team_name):
    data = openfoot_get(
        "/v1/search",
        params={"q": team_name},
    )

    results = data.get("data", [])

    if not results:
        return None

    # Prefer team entities.
    team_results = []

    for item in results:
        entity_type = (
            item.get("type")
            or item.get("entityType")
            or item.get("kind")
        )

        if entity_type == "team":
            team_results.append(item)

    if team_results:
        results = team_results

    return results[0]


# ============================================================
# MATCH PARSING
# ============================================================

def parse_match(match, team_id=None):
    home = match.get("homeTeam") or {}
    away = match.get("awayTeam") or {}

    score = match.get("score") or {}

    home_goals = score.get("home")
    away_goals = score.get("away")

    if home_goals is None or away_goals is None:
        return None

    home_id = home.get("id")
    away_id = away.get("id")

    if team_id:
        if team_id == home_id:
            venue = "home"
            gf = home_goals
            ga = away_goals
        elif team_id == away_id:
            venue = "away"
            gf = away_goals
            ga = home_goals
        else:
            return None
    else:
        venue = None
        gf = home_goals
        ga = away_goals

    if gf > ga:
        result = "W"
    elif gf == ga:
        result = "D"
    else:
        result = "L"

    return {
        "id": match.get("id"),
        "kickoff": match.get("kickoffAt"),
        "home": home.get("name", "Unknown"),
        "away": away.get("name", "Unknown"),
        "home_id": home_id,
        "away_id": away_id,
        "gf": gf,
        "ga": ga,
        "venue": venue,
        "result": result,
    }


# ============================================================
# GET RECENT MATCHES
# ============================================================

def get_recent_matches(team_id, limit=RECENT_MATCHES):
    data = openfoot_get(
        "/v1/matches",
        params={
            "team": team_id,
            "season": CURRENT_SEASON,
            "status": "finished",
        },
    )

    matches = data.get("data", [])

    parsed = []

    for match in matches:
        item = parse_match(match, team_id)

        if item:
            parsed.append(item)

    parsed.sort(
        key=lambda x: x.get("kickoff") or "",
        reverse=True,
    )

    return parsed[:limit]


# ============================================================
# GET TEAM FIXTURES
# ============================================================

def get_team_fixtures(team_id):
    data = openfoot_get(
        "/v1/matches",
        params={
            "team": team_id,
            "season": CURRENT_SEASON,
        },
    )

    matches = data.get("data", [])

    matches.sort(
        key=lambda x: x.get("kickoffAt") or ""
    )

    return matches


# ============================================================
# BASIC STATS
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

    sample = len(matches)

    wins = sum(1 for m in matches if m["result"] == "W")
    draws = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")

    gf = sum(m["gf"] for m in matches)
    ga = sum(m["ga"] for m in matches)

    scoring = sum(
        1 for m in matches
        if m["gf"] > 0
    )

    clean_sheet = sum(
        1 for m in matches
        if m["ga"] == 0
    )

    over_1_5 = sum(
        1 for m in matches
        if (m["gf"] + m["ga"]) > 1
    )

    over_2_5 = sum(
        1 for m in matches
        if (m["gf"] + m["ga"]) > 2
    )

    under_3_5 = sum(
        1 for m in matches
        if (m["gf"] + m["ga"]) < 4
    )

    btts = sum(
        1 for m in matches
        if m["gf"] > 0 and m["ga"] > 0
    )

    def pct(value):
        return round((value / sample) * 100)

    return {
        "sample": sample,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf,
        "ga": ga,
        "avg_gf": gf / sample,
        "avg_ga": ga / sample,
        "scoring": pct(scoring),
        "clean_sheet": pct(clean_sheet),
        "over_1_5": pct(over_1_5),
        "over_2_5": pct(over_2_5),
        "under_3_5": pct(under_3_5),
        "btts": pct(btts),
    }


# ============================================================
# VENUE STATS
# ============================================================

def venue_stats(matches, venue):
    filtered = [
        m for m in matches
        if m["venue"] == venue
    ]

    return calculate_stats(filtered)


# ============================================================
# CONFIDENCE ENGINE
# ============================================================

def clamp(value, low=MIN_CONFIDENCE, high=MAX_CONFIDENCE):
    return max(low, min(high, value))


def sample_reliability(sample):
    """
    Reliability of a sample.

    0-1 matches: very weak
    2 matches: weak
    3 matches: moderate
    4 matches: good
    5+: strong
    """

    if sample <= 1:
        return 0.25

    if sample == 2:
        return 0.40

    if sample == 3:
        return 0.55

    if sample == 4:
        return 0.70

    return 0.85


def conservative_confidence(raw_score, samples):
    """
    Pulls a raw signal toward 50% according to
    sample reliability.
    """

    if not samples:
        return 50

    reliability = mean(
        sample_reliability(s)
        for s in samples
    )

    confidence = (
        50
        + ((raw_score - 50) * reliability)
    )

    return clamp(confidence)


# ============================================================
# EVIDENCE AGREEMENT
# ============================================================

def evidence_agreement(values):
    """
    Measures how closely the independent indicators agree.

    Returns:
        agreement score from 0 to 1
    """

    clean = [
        float(v)
        for v in values
        if v is not None
    ]

    if len(clean) < 2:
        return 0

    spread = max(clean) - min(clean)

    if spread <= 5:
        return 1.0

    if spread <= 10:
        return 0.85

    if spread <= 15:
        return 0.65

    if spread <= 20:
        return 0.40

    return 0.15


def apply_agreement_bonus(
    confidence,
    evidence_values,
    samples,
):
    """
    v2.2 improvement.

    Strong agreement can restore a small number of
    points lost because of small samples.

    The bonus is deliberately limited so the model
    cannot jump from weak evidence to an extreme
    confidence level.
    """

    agreement = evidence_agreement(
        evidence_values
    )

    if not samples:
        return confidence

    avg_reliability = mean(
        sample_reliability(s)
        for s in samples
    )

    # Agreement bonus is strongest when:
    # - indicators agree closely
    # - samples are at least moderately useful
    #
    # Maximum bonus = 5 points.
    bonus = (
        agreement
        * avg_reliability
        * 6
    )

    return clamp(
        round(confidence + bonus)
    )


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_markets(
    team1_matches,
    team2_matches,
    team1_home,
    team2_away,
):
    s1 = calculate_stats(team1_matches)
    s2 = calculate_stats(team2_matches)

    h1 = calculate_stats(team1_home)
    a2 = calculate_stats(team2_away)

    markets = {}

    # --------------------------------------------------------
    # 1X
    # --------------------------------------------------------

    team1_unbeaten_overall = (
        ((s1["wins"] + s1["draws"]) / s1["sample"]) * 100
        if s1["sample"]
        else 50
    )

    team1_unbeaten_home = (
        ((h1["wins"] + h1["draws"]) / h1["sample"]) * 100
        if h1["sample"]
        else 50
    )

    team2_away_losses = (
        (a2["losses"] / a2["sample"]) * 100
        if a2["sample"]
        else 50
    )

    raw_1x = mean([
        team1_unbeaten_overall,
        team1_unbeaten_home,
        team2_away_losses,
    ])

    conf_1x = conservative_confidence(
        raw_1x,
        [
            s1["sample"],
            h1["sample"],
            a2["sample"],
        ],
    )

    conf_1x = apply_agreement_bonus(
        conf_1x,
        [
            team1_unbeaten_overall,
            team1_unbeaten_home,
            100 - team2_away_losses,
        ],
        [
            s1["sample"],
            h1["sample"],
            a2["sample"],
        ],
    )

    markets["1X"] = conf_1x

    # --------------------------------------------------------
    # X2
    # --------------------------------------------------------

    team2_unbeaten_overall = (
        ((s2["wins"] + s2["draws"]) / s2["sample"]) * 100
        if s2["sample"]
        else 50
    )

    team2_unbeaten_away = (
        ((a2["wins"] + a2["draws"]) / a2["sample"]) * 100
        if a2["sample"]
        else 50
    )

    team1_home_losses = (
        (h1["losses"] / h1["sample"]) * 100
        if h1["sample"]
        else 50
    )

    raw_x2 = mean([
        team2_unbeaten_overall,
        team2_unbeaten_away,
        team1_home_losses,
    ])

    conf_x2 = conservative_confidence(
        raw_x2,
        [
            s2["sample"],
            a2["sample"],
            h1["sample"],
        ],
    )

    conf_x2 = apply_agreement_bonus(
        conf_x2,
        [
            team2_unbeaten_overall,
            team2_unbeaten_away,
            100 - team1_home_losses,
        ],
        [
            s2["sample"],
            a2["sample"],
            h1["sample"],
        ],
    )

    markets["X2"] = conf_x2

    # --------------------------------------------------------
    # GOAL ENVIRONMENT MARKETS
    # --------------------------------------------------------

    def goal_market(key):
        values = [
            s1[key],
            s2[key],
            h1[key],
            a2[key],
        ]

        raw = mean(values)

        confidence = conservative_confidence(
            raw,
            [
                s1["sample"],
                s2["sample"],
                h1["sample"],
                a2["sample"],
            ],
        )

        confidence = apply_agreement_bonus(
            confidence,
            values,
            [
                s1["sample"],
                s2["sample"],
                h1["sample"],
                a2["sample"],
            ],
        )

        return confidence

    markets["O1.5"] = goal_market("over_1_5")
    markets["O2.5"] = goal_market("over_2_5")
    markets["U3.5"] = goal_market("under_3_5")
    markets["BTTS"] = goal_market("btts")

    # --------------------------------------------------------
    # TEAM 1 TO SCORE
    # --------------------------------------------------------

    team1_scoring_values = [
        s1["scoring"],
        100 - s2["clean_sheet"],
        h1["scoring"],
        100 - a2["clean_sheet"],
    ]

    raw_team1_score = mean(
        team1_scoring_values
    )

    team1_score = conservative_confidence(
        raw_team1_score,
        [
            s1["sample"],
            s2["sample"],
            h1["sample"],
            a2["sample"],
        ],
    )

    team1_score = apply_agreement_bonus(
        team1_score,
        team1_scoring_values,
        [
            s1["sample"],
            s2["sample"],
            h1["sample"],
            a2["sample"],
        ],
    )

    # Penalize if team 1 venue scoring is poor.
    if h1["sample"] >= 2 and h1["scoring"] <= 50:
        team1_score -= 5

    # Penalize if opponent venue clean sheets are strong.
    if a2["sample"] >= 2 and a2["clean_sheet"] >= 67:
        team1_score -= 5

    markets["Team1 score"] = clamp(
        round(team1_score)
    )

    # --------------------------------------------------------
    # TEAM 2 TO SCORE
    # --------------------------------------------------------

    team2_scoring_values = [
        s2["scoring"],
        100 - s1["clean_sheet"],
        a2["scoring"],
        100 - h1["clean_sheet"],
    ]

    raw_team2_score = mean(
        team2_scoring_values
    )

    team2_score = conservative_confidence(
        raw_team2_score,
        [
            s2["sample"],
            s1["sample"],
            a2["sample"],
            h1["sample"],
        ],
    )

    team2_score = apply_agreement_bonus(
        team2_score,
        team2_scoring_values,
        [
            s2["sample"],
            s1["sample"],
            a2["sample"],
            h1["sample"],
        ],
    )

    if a2["sample"] >= 2 and a2["scoring"] <= 50:
        team2_score -= 5

    if h1["sample"] >= 2 and h1["clean_sheet"] >= 67:
        team2_score -= 5

    markets["Team2 score"] = clamp(
        round(team2_score)
    )

    # Final cleanup.
    for key in markets:
        markets[key] = clamp(
            round(markets[key])
        )

    return markets


# ============================================================
# PRIMARY SIGNAL
# ============================================================

def choose_primary(markets):
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

    candidates = [
        (market, confidence)
        for market, confidence in markets.items()
        if confidence >= 68
    ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item[1],
            priority.get(item[0], 0),
        ),
        reverse=True,
    )

    return candidates[0]


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
        f"{name} last {stats['sample']}: {form}, "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']}, "
        f"GF{stats['gf']} GA{stats['ga']}, "
        f"avg {stats['avg_gf']:.2f}/{stats['avg_ga']:.2f}, "
        f"O1.5 {stats['over_1_5']}%, "
        f"O2.5 {stats['over_2_5']}%, "
        f"U3.5 {stats['under_3_5']}%, "
        f"BTTS {stats['btts']}%, "
        f"scoring {stats['scoring']}%, "
        f"CS {stats['clean_sheet']}%"
    )


def format_venue_stats(name, venue, stats):
    if stats["sample"] == 0:
        return f"{name} {venue.upper()} sample 0: no available matches"

    return (
        f"{name} {venue.upper()} sample {stats['sample']}: "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']}, "
        f"scoring {stats['scoring']}%, "
        f"CS {stats['clean_sheet']}%, "
        f"O2.5 {stats['over_2_5']}%, "
        f"BTTS {stats['btts']}%"
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        "⚽ Welcome to GoalLogic AI v2.2!\n\n"
        "I analyze football matches using recent "
        "form, home/away performance and evidence agreement.\n\n"
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
        openfoot_get(
            "/v1/search",
            params={"q": "Chelsea"},
        )

        message = (
            "🔧 OPENFOOT TEST PASSED\n\n"
            "OpenFoot API is connected successfully."
        )

    except Exception as e:
        message = (
            "❌ OPENFOOT TEST FAILED\n\n"
            f"{e}"
        )

    await update.message.reply_text(message)


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

    team_name = " ".join(context.args)

    try:
        team = search_team(team_name)

        if not team:
            await update.message.reply_text(
                f"❌ Team not found: {team_name}"
            )
            return

        name = (
            team.get("name")
            or team.get("displayName")
            or team_name
        )

        team_id = (
            team.get("id")
            or team.get("teamId")
        )

        country = (
            team.get("country")
            or "Unknown"
        )

        message = (
            "⚽ TEAM SEARCH\n\n"
            f"Team: {name}\n"
            f"ID: {team_id}\n"
            f"Country: {country}"
        )

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(
            f"❌ Team search error:\n{e}"
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

    team_name = " ".join(context.args)

    try:
        team = search_team(team_name)

        if not team:
            await update.message.reply_text(
                f"❌ Team not found: {team_name}"
            )
            return

        team_id = team.get("id")

        matches = get_team_fixtures(team_id)

        if not matches:
            await update.message.reply_text(
                f"❌ No fixtures found for {team_name}."
            )
            return

        actual_name = (
            team.get("name")
            or team.get("displayName")
            or team_name
        )

        lines = [
            f"📅 {actual_name} — {CURRENT_SEASON}\n"
        ]

        for match in matches[:10]:
            home = (
                match.get("homeTeam") or {}
            ).get("name", "Unknown")

            away = (
                match.get("awayTeam") or {}
            ).get("name", "Unknown")

            kickoff = (
                match.get("kickoffAt")
                or "Unknown time"
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
    context: ContextTypes.DEFAULT_TYPE,
):
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    full_text = " ".join(context.args)

    parts = full_text.split(
        " vs ",
        1,
    )

    if len(parts) != 2:
        parts = full_text.split(
            " v ",
            1,
        )

    if len(parts) != 2:
        await update.message.reply_text(
            "Please use:\n"
            "/analyze Chelsea vs Arsenal"
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

        team1_actual = (
            team1.get("name")
            or team1.get("displayName")
            or team1_name
        )

        team2_actual = (
            team2.get("name")
            or team2.get("displayName")
            or team2_name
        )

        team1_matches = get_recent_matches(
            team1_id
        )

        team2_matches = get_recent_matches(
            team2_id
        )

        if not team1_matches:
            await update.message.reply_text(
                f"❌ No recent matches found for {team1_actual}."
            )
            return

        if not team2_matches:
            await update.message.reply_text(
                f"❌ No recent matches found for {team2_actual}."
            )
            return

        team1_home = [
            m for m in team1_matches
            if m["venue"] == "home"
        ]

        team2_away = [
            m for m in team2_matches
            if m["venue"] == "away"
        ]

        s1 = calculate_stats(team1_matches)
        s2 = calculate_stats(team2_matches)

        h1 = calculate_stats(team1_home)
        a2 = calculate_stats(team2_away)

        markets = analyze_markets(
            team1_matches,
            team2_matches,
            team1_home,
            team2_away,
        )

        primary = choose_primary(markets)

        lines = [
            "⚽ GOALLOGIC AI v2.2",
            "",
            f"🏟️ {team1_actual} vs {team2_actual}",
            "",
            f"Season: {CURRENT_SEASON}",
            f"Sample: Last {RECENT_MATCHES} available matches",
            "",
            format_stats(
                team1_actual,
                s1,
            ),
            "",
            format_stats(
                team2_actual,
                s2,
            ),
            "",
            format_venue_stats(
                team1_actual,
                "home",
                h1,
            ),
            format_venue_stats(
                team2_actual,
                "away",
                a2,
            ),
            "",
            "📊 MARKETS",
            f"{'🟢' if markets['1X'] >= 68 else '🟡' if markets['1X'] >= 60 else '🔴'} 1X: {markets['1X']}%",
            f"{'🟢' if markets['X2'] >= 68 else '🟡' if markets['X2'] >= 60 else '🔴'} X2: {markets['X2']}%",
            f"{'🟢' if markets['O1.5'] >= 68 else '🟡' if markets['O1.5'] >= 60 else '🔴'} O1.5: {markets['O1.5']}%",
            f"{'🟢' if markets['O2.5'] >= 68 else '🟡' if markets['O2.5'] >= 60 else '🔴'} O2.5: {markets['O2.5']}%",
            f"{'🟢' if markets['U3.5'] >= 68 else '🟡' if markets['U3.5'] >= 60 else '🔴'} U3.5: {markets['U3.5']}%",
            f"{'🟢' if markets['BTTS'] >= 68 else '🟡' if markets['BTTS'] >= 60 else '🔴'} BTTS: {markets['BTTS']}%",
            f"{'🟢' if markets['Team1 score'] >= 68 else '🟡' if markets['Team1 score'] >= 60 else '🔴'} Team1 score: {markets['Team1 score']}%",
            f"{'🟢' if markets['Team2 score'] >= 68 else '🟡' if markets['Team2 score'] >= 60 else '🔴'} Team2 score: {markets['Team2 score']}%",
            "",
        ]

        if primary:
            market, confidence = primary

            lines.extend([
                "🎯 PRIMARY SIGNAL",
                f"{market} — {confidence}%",
                "",
                "📌 Advice: BET only if the available odds justify the risk.",
                "",
                "⚠️ Statistical signal only — not a guarantee.",
            ])

        else:
            lines.extend([
                "⚠️ NO STRONG PRIMARY SIGNAL",
                "",
                "The available sample does not provide enough evidence for a high-confidence market.",
                "",
                "📌 Advice: AVOID.",
            ])

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Analysis error:\n{e}"
        )


# ============================================================
# MAIN
# ============================================================

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise Exception(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not OPENFOOT_API_KEY:
        raise Exception(
            "OPENFOOT_API_KEY is missing."
        )

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "apitest",
            apitest_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "team",
            team_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "fixtures",
            fixtures_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "analyze",
            analyze_command,
        )
    )

    print("GoalLogic AI v2.2 Telegram bot starting...")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
