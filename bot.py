import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from statistics import mean
from math import exp

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# GOALLOGIC AI v2.6
# ============================================================

API_BASE = "https://openfootapi.com/v1"
SEASON = "2026/27"
SAMPLE_SIZE = 5
PORT = int(os.environ.get("PORT", "10000"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.environ.get("OPENFOOT_API_KEY")


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GoalLogic AI v2.6 is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"GoalLogic AI v2.6 is running on port {PORT}.")
    server.serve_forever()


# ============================================================
# API
# ============================================================

def api_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        raise Exception("OPENFOOT_API_KEY is missing.")

    url = f"{API_BASE}{endpoint}"

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
        raise Exception(
            f"API HTTP {response.status_code}: {response.text[:500]}"
        )

    try:
        payload = response.json()
    except Exception:
        raise Exception("API returned invalid JSON.")

    return payload.get("data", payload)


# ============================================================
# TEAM SEARCH
# ============================================================

def find_team(team_name):
    data = api_get(
        "/search",
        {"q": team_name},
    )

    if not isinstance(data, list):
        raise Exception("Unexpected team search response.")

    team_results = [
        item for item in data
        if isinstance(item, dict)
        and str(item.get("type", "")).lower() == "team"
    ]

    if not team_results:
        team_results = data

    if not team_results:
        raise Exception(f"No team found for: {team_name}")

    exact = None

    for team in team_results:
        name = str(
            team.get("name")
            or team.get("teamName")
            or ""
        ).strip().lower()

        if name == team_name.strip().lower():
            exact = team
            break

    selected = exact or team_results[0]

    team_id = (
        selected.get("id")
        or selected.get("teamId")
        or selected.get("team_id")
    )

    if not team_id:
        raise Exception(f"Team ID missing for {team_name}")

    return {
        "id": team_id,
        "name": (
            selected.get("name")
            or selected.get("teamName")
            or team_name
        ),
        "country": selected.get("country") or "",
    }


# ============================================================
# MATCH PARSING
# ============================================================

def extract_score(match, home=True):
    score = match.get("score") or {}

    if not isinstance(score, dict):
        return None

    side = "home" if home else "away"

    candidates = [
        score.get(side),
        score.get(f"{side}Goals"),
        score.get(f"{side}_goals"),
        score.get(f"{side}Score"),
        score.get(f"{side}_score"),
    ]

    for value in candidates:
        if isinstance(value, dict):
            value = (
                value.get("current")
                or value.get("goals")
                or value.get("total")
                or value.get("value")
            )

        if value is not None:
            try:
                return int(value)
            except Exception:
                pass

    return None


def parse_match(match):
    if not isinstance(match, dict):
        return None

    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    if not isinstance(home_team, dict):
        home_team = {}

    if not isinstance(away_team, dict):
        away_team = {}

    home_id = (
        home_team.get("id")
        or home_team.get("teamId")
        or match.get("homeTeamId")
    )

    away_id = (
        away_team.get("id")
        or away_team.get("teamId")
        or match.get("awayTeamId")
    )

    home_name = (
        home_team.get("name")
        or home_team.get("teamName")
        or "Home"
    )

    away_name = (
        away_team.get("name")
        or away_team.get("teamName")
        or "Away"
    )

    home_score = extract_score(match, True)
    away_score = extract_score(match, False)

    kickoff = (
        match.get("kickoffAt")
        or match.get("date")
        or match.get("fixtureDate")
        or ""
    )

    status = str(
        match.get("status")
        or match.get("state")
        or ""
    ).lower()

    return {
        "home_id": home_id,
        "away_id": away_id,
        "home_name": home_name,
        "away_name": away_name,
        "home_score": home_score,
        "away_score": away_score,
        "kickoff": kickoff,
        "status": status,
    }


# ============================================================
# TEAM MATCHES
# ============================================================

def get_team_matches(team_id):
    data = api_get(
        "/matches",
        {
            "team": team_id,
            "season": SEASON,
        },
    )

    if not isinstance(data, list):
        raise Exception("Unexpected matches response.")

    matches = []

    completed_statuses = {
        "finished",
        "completed",
        "fulltime",
        "ft",
        "ended",
        "complete",
        "",
    }

    for raw in data:
        parsed = parse_match(raw)

        if not parsed:
            continue

        if parsed["home_score"] is None:
            continue

        if parsed["away_score"] is None:
            continue

        status = parsed["status"]

        if status not in completed_statuses:
            continue

        matches.append(parsed)

    matches.sort(
        key=lambda x: str(x.get("kickoff", "")),
        reverse=True,
    )

    return matches


# ============================================================
# BASIC STATS
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
            "o15": 0,
            "o25": 0,
            "u35": 0,
            "btts": 0,
            "scoring": 0,
            "clean_sheet": 0,
            "unbeaten": 0,
            "loss_rate": 0,
            "form": "",
        }

    wins = 0
    draws = 0
    losses = 0
    gf = 0
    ga = 0

    o15 = 0
    o25 = 0
    u35 = 0
    btts = 0
    scoring = 0
    clean_sheet = 0
    unbeaten = 0

    form = []

    for m in matches:
        hs = m["home_score"]
        aws = m["away_score"]

        if hs is None or aws is None:
            continue

        total = hs + aws

        gf += hs
        ga += aws

        if hs > aws:
            wins += 1
            form.append("W")
        elif hs == aws:
            draws += 1
            form.append("D")
        else:
            losses += 1
            form.append("L")

        if total > 1.5:
            o15 += 1

        if total > 2.5:
            o25 += 1

        if total < 3.5:
            u35 += 1

        if hs > 0 and aws > 0:
            btts += 1

        if hs > 0:
            scoring += 1

        if aws == 0:
            clean_sheet += 1

        if hs >= aws:
            unbeaten += 1

    games = len(matches)

    return {
        "games": games,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf,
        "ga": ga,
        "avg_gf": gf / games,
        "avg_ga": ga / games,
        "o15": o15 / games * 100,
        "o25": o25 / games * 100,
        "u35": u35 / games * 100,
        "btts": btts / games * 100,
        "scoring": scoring / games * 100,
        "clean_sheet": clean_sheet / games * 100,
        "unbeaten": unbeaten / games * 100,
        "loss_rate": losses / games * 100,
        "form": "".join(form),
    }


# ============================================================
# HOME / AWAY FILTERS
# ============================================================

def get_home_matches(matches, team_id):
    return [
        m for m in matches
        if m["home_id"] == team_id
    ]


def get_away_matches(matches, team_id):
    return [
        m for m in matches
        if m["away_id"] == team_id
    ]


# ============================================================
# TEAM-SPECIFIC STATS
# ============================================================

def team_oriented_stats(matches, team_id):
    if not matches:
        return calculate_stats([])

    converted = []

    for m in matches:
        if m["home_id"] == team_id:
            converted.append({
                "home_score": m["home_score"],
                "away_score": m["away_score"],
            })

        elif m["away_id"] == team_id:
            converted.append({
                "home_score": m["away_score"],
                "away_score": m["home_score"],
            })

    return calculate_stats(converted)


# ============================================================
# SAMPLE WEIGHT
# ============================================================

def sample_weight(n):
    if n <= 0:
        return 0.20

    if n == 1:
        return 0.30

    if n == 2:
        return 0.40

    if n == 3:
        return 0.50

    if n == 4:
        return 0.60

    return 0.70


# ============================================================
# PROBABILITY CALIBRATION
# ============================================================

def clamp(value, low=5, high=95):
    return max(low, min(high, value))


def shrink_probability(raw, n, centre=50):
    """
    Pulls extreme probabilities toward 50 when the sample is small.
    This prevents 2-3 match venue samples from creating unrealistic
    95-99% probabilities.
    """

    weight = sample_weight(n)

    return centre + (raw - centre) * weight


def agreement_adjustment(probabilities):
    """
    Small adjustment for agreement between independent indicators.

    v2.6 deliberately uses a much smaller adjustment than v2.5.
    """

    if not probabilities:
        return 0

    spread = max(probabilities) - min(probabilities)

    if spread <= 10:
        return 3

    if spread <= 20:
        return 1

    if spread >= 40:
        return -3

    return 0


# ============================================================
# POISSON HELPERS
# ============================================================

def poisson_probability(lam, goals):
    if lam < 0:
        lam = 0

    return exp(-lam) * (lam ** goals)


def probability_team_scores(lam):
    """
    Probability of at least one goal:
    P(X >= 1) = 1 - P(X = 0)
    """

    probability = 1 - exp(-max(0, lam))

    return probability * 100


def probability_over_15(total_xg):
    return (1 - exp(-total_xg) * (1 + total_xg)) * 100


def probability_over_25(total_xg):
    probability_under_3 = (
        exp(-total_xg)
        * (
            1
            + total_xg
            + (total_xg ** 2) / 2
        )
    )

    return (1 - probability_under_3) * 100


def probability_under_35(total_xg):
    probability_under_4 = (
        exp(-total_xg)
        * (
            1
            + total_xg
            + (total_xg ** 2) / 2
            + (total_xg ** 3) / 6
        )
    )

    return probability_under_4 * 100


def probability_btts(home_xg, away_xg):
    home_scores = 1 - exp(-home_xg)
    away_scores = 1 - exp(-away_xg)

    return home_scores * away_scores * 100


# ============================================================
# EXPECTED GOALS
# ============================================================

def calculate_xg(team1_stats, team2_stats, team1_home, team2_away):
    """
    Conservative xG calculation.

    Uses attack + opponent defensive concession rates,
    then blends venue evidence where available.
    """

    team1_attack = team1_stats["avg_gf"]
    team1_defense = team1_stats["avg_ga"]

    team2_attack = team2_stats["avg_gf"]
    team2_defense = team2_stats["avg_ga"]

    home_attack = team1_home["avg_gf"] if team1_home["games"] else team1_attack
    home_defense = team1_home["avg_ga"] if team1_home["games"] else team1_defense

    away_attack = team2_away["avg_gf"] if team2_away["games"] else team2_attack
    away_defense = team2_away["avg_ga"] if team2_away["games"] else team2_defense

    team1_base = (
        team1_attack * 0.55
        + team2_defense * 0.45
    )

    team2_base = (
        team2_attack * 0.55
        + team1_defense * 0.45
    )

    team1_venue = (
        home_attack * 0.55
        + away_defense * 0.45
    )

    team2_venue = (
        away_attack * 0.55
        + home_defense * 0.45
    )

    team1_weight = sample_weight(team1_home["games"])
    team2_weight = sample_weight(team2_away["games"])

    team1_xg = (
        team1_base * (1 - team1_weight * 0.35)
        + team1_venue * (team1_weight * 0.35)
    )

    team2_xg = (
        team2_base * (1 - team2_weight * 0.35)
        + team2_venue * (team2_weight * 0.35)
    )

    team1_xg = max(0.20, min(3.50, team1_xg))
    team2_xg = max(0.20, min(3.50, team2_xg))

    return team1_xg, team2_xg


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_markets(
    team1_stats,
    team2_stats,
    team1_home,
    team2_away,
    team1_xg,
    team2_xg,
):
    total_xg = team1_xg + team2_xg

    # -------------------------
    # xG probabilities
    # -------------------------

    xg_team1_score = probability_team_scores(team1_xg)
    xg_team2_score = probability_team_scores(team2_xg)

    xg_o15 = probability_over_15(total_xg)
    xg_o25 = probability_over_25(total_xg)
    xg_u35 = probability_under_35(total_xg)
    xg_btts = probability_btts(team1_xg, team2_xg)

    # -------------------------
    # Historical probabilities
    # -------------------------

    team1_score_hist = mean([
        team1_stats["scoring"],
        team1_home["scoring"] if team1_home["games"] else team1_stats["scoring"],
        100 - team2_stats["clean_sheet"],
        100 - team2_away["clean_sheet"]
        if team2_away["games"]
        else 100 - team2_stats["clean_sheet"],
    ])

    team2_score_hist = mean([
        team2_stats["scoring"],
        team2_away["scoring"] if team2_away["games"] else team2_stats["scoring"],
        100 - team1_stats["clean_sheet"],
        100 - team1_home["clean_sheet"]
        if team1_home["games"]
        else 100 - team1_stats["clean_sheet"],
    ])

    o15_hist = mean([
        team1_stats["o15"],
        team2_stats["o15"],
        team1_home["o15"] if team1_home["games"] else team1_stats["o15"],
        team2_away["o15"] if team2_away["games"] else team2_stats["o15"],
    ])

    o25_hist = mean([
        team1_stats["o25"],
        team2_stats["o25"],
        team1_home["o25"] if team1_home["games"] else team1_stats["o25"],
        team2_away["o25"] if team2_away["games"] else team2_stats["o25"],
    ])

    u35_hist = mean([
        team1_stats["u35"],
        team2_stats["u35"],
        team1_home["u35"] if team1_home["games"] else team1_stats["u35"],
        team2_away["u35"] if team2_away["games"] else team2_stats["u35"],
    ])

    btts_hist = mean([
        team1_stats["btts"],
        team2_stats["btts"],
        team1_home["btts"] if team1_home["games"] else team1_stats["btts"],
        team2_away["btts"] if team2_away["games"] else team2_stats["btts"],
    ])

    # -------------------------
    # Blend xG + history
    # -------------------------

    def blend(xg_value, hist_value, venue_n):
        weight = sample_weight(venue_n)

        result = (
            xg_value * 0.65
            + hist_value * 0.35
        )

        result = shrink_probability(
            result,
            max(venue_n, 3),
        )

        return clamp(result, 5, 95)

    team1_score = blend(
        xg_team1_score,
        team1_score_hist,
        team1_home["games"],
    )

    team2_score = blend(
        xg_team2_score,
        team2_score_hist,
        team2_away["games"],
    )

    o15 = blend(
        xg_o15,
        o15_hist,
        min(team1_home["games"], team2_away["games"])
        if team1_home["games"] and team2_away["games"]
        else 3,
    )

    o25 = blend(
        xg_o25,
        o25_hist,
        min(team1_home["games"], team2_away["games"])
        if team1_home["games"] and team2_away["games"]
        else 3,
    )

    u35 = blend(
        xg_u35,
        u35_hist,
        min(team1_home["games"], team2_away["games"])
        if team1_home["games"] and team2_away["games"]
        else 3,
    )

    btts = blend(
        xg_btts,
        btts_hist,
        min(team1_home["games"], team2_away["games"])
        if team1_home["games"] and team2_away["games"]
        else 3,
    )

    # -------------------------
    # Result markets
    # -------------------------

    team1_unbeaten = mean([
        team1_stats["unbeaten"],
        team1_home["unbeaten"]
        if team1_home["games"]
        else team1_stats["unbeaten"],
        100 - team2_stats["wins"],
        100 - (
            team2_away["wins"]
            if team2_away["games"]
            else team2_stats["wins"]
        ),
    ])

    team2_unbeaten = mean([
        team2_stats["unbeaten"],
        team2_away["unbeaten"]
        if team2_away["games"]
        else team2_stats["unbeaten"],
        100 - team1_stats["wins"],
        100 - (
            team1_home["wins"]
            if team1_home["games"]
            else team1_stats["wins"]
        ),
    ])

    # xG-inspired result probabilities.
    # Draw probability is explicitly preserved.
    home_strength = max(0.01, team1_xg)
    away_strength = max(0.01, team2_xg)

    total_strength = home_strength + away_strength

    home_share = home_strength / total_strength
    away_share = away_strength / total_strength

    draw_base = max(
        0.15,
        min(
            0.35,
            0.30 - abs(home_strength - away_strength) * 0.04
        )
    )

    home_result_xg = (1 - draw_base) * home_share
    away_result_xg = (1 - draw_base) * away_share

    x1_xg = (home_result_xg + draw_base) * 100
    x2_xg = (away_result_xg + draw_base) * 100

    x1 = (
        x1_xg * 0.60
        + team1_unbeaten * 0.40
    )

    x2 = (
        x2_xg * 0.60
        + team2_unbeaten * 0.40
    )

    x1 = shrink_probability(
        x1,
        team1_home["games"],
    )

    x2 = shrink_probability(
        x2,
        team2_away["games"],
    )

    x1 = clamp(x1)
    x2 = clamp(x2)

    return {
        "1X": x1,
        "X2": x2,
        "O1.5": o15,
        "O2.5": o25,
        "U3.5": u35,
        "BTTS": btts,
        "Team1 score": team1_score,
        "Team2 score": team2_score,
    }


# ============================================================
# RESULT MARKET CONFLICT
# ============================================================

def detect_result_conflict(markets):
    one_x = markets["1X"]
    x_two = markets["X2"]

    return (
        one_x >= 65
        and x_two >= 65
        and abs(one_x - x_two) <= 8
    )


# ============================================================
# SIGNAL STRENGTH
# ============================================================

def signal_strength(confidence):
    if confidence >= 80:
        return "Strong"

    if confidence >= 65:
        return "Moderate"

    return "Weak"


def signal_score(confidence):
    """
    Used for ranking only.
    Confidence remains the main ranking factor.
    """

    return confidence


def top_signals(markets, conflict):
    excluded = set()

    if conflict:
        excluded.update(["1X", "X2"])

    candidates = []

    for market, confidence in markets.items():
        if market in excluded:
            continue

        if confidence < 60:
            continue

        strength = signal_strength(confidence)

        candidates.append({
            "market": market,
            "confidence": confidence,
            "strength": strength,
            "sort_score": signal_score(confidence),
        })

    candidates.sort(
        key=lambda x: x["sort_score"],
        reverse=True,
    )

    return candidates[:3]


def choose_primary(markets, conflict):
    signals = top_signals(markets, conflict)

    if not signals:
        return None

    best = signals[0]

    if best["confidence"] < 60:
        return None

    return best


# ============================================================
# FORMATTING
# ============================================================

def pct(value):
    return f"{round(value):.0f}%"


def format_stats_line(name, stats):
    return (
        f"{name} last 5: {stats['form']}, "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']}, "
        f"GF{stats['gf']} GA{stats['ga']}, "
        f"avg {stats['avg_gf']:.2f}/{stats['avg_ga']:.2f}, "
        f"O1.5 {pct(stats['o15'])}, "
        f"O2.5 {pct(stats['o25'])}, "
        f"U3.5 {pct(stats['u35'])}, "
        f"BTTS {pct(stats['btts'])}, "
        f"scoring {pct(stats['scoring'])}, "
        f"CS {pct(stats['clean_sheet'])}"
    )


def format_venue_line(name, label, stats):
    return (
        f"{name} {label} sample {stats['games']}: "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']}, "
        f"scoring {pct(stats['scoring'])}, "
        f"CS {pct(stats['clean_sheet'])}, "
        f"O2.5 {pct(stats['o25'])}, "
        f"BTTS {pct(stats['btts'])}"
    )


def market_icon(confidence):
    if confidence >= 75:
        return "🟢"

    if confidence >= 60:
        return "🟡"

    return "🔴"


# ============================================================
# ANALYSIS
# ============================================================

def analyze_match(team1_name, team2_name):
    team1 = find_team(team1_name)
    team2 = find_team(team2_name)

    team1_matches = get_team_matches(team1["id"])
    team2_matches = get_team_matches(team2["id"])

    if not team1_matches:
        raise Exception(
            f"No completed {SEASON} matches found for {team1_name}."
        )

    if not team2_matches:
        raise Exception(
            f"No completed {SEASON} matches found for {team2_name}."
        )

    team1_matches = team1_matches[:SAMPLE_SIZE]
    team2_matches = team2_matches[:SAMPLE_SIZE]

    team1_home_matches = get_home_matches(
        team1_matches,
        team1["id"],
    )

    team2_away_matches = get_away_matches(
        team2_matches,
        team2["id"],
    )

    team1_stats = team_oriented_stats(
        team1_matches,
        team1["id"],
    )

    team2_stats = team_oriented_stats(
        team2_matches,
        team2["id"],
    )

    team1_home = team_oriented_stats(
        team1_home_matches,
        team1["id"],
    )

    team2_away = team_oriented_stats(
        team2_away_matches,
        team2["id"],
    )

    team1_xg, team2_xg = calculate_xg(
        team1_stats,
        team2_stats,
        team1_home,
        team2_away,
    )

    markets = analyze_markets(
        team1_stats,
        team2_stats,
        team1_home,
        team2_away,
        team1_xg,
        team2_xg,
    )

    conflict = detect_result_conflict(markets)

    signals = top_signals(
        markets,
        conflict,
    )

    primary = choose_primary(
        markets,
        conflict,
    )

    return {
        "team1": team1,
        "team2": team2,
        "team1_stats": team1_stats,
        "team2_stats": team2_stats,
        "team1_home": team1_home,
        "team2_away": team2_away,
        "team1_xg": team1_xg,
        "team2_xg": team2_xg,
        "markets": markets,
        "conflict": conflict,
        "signals": signals,
        "primary": primary,
    }


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "⚽ Welcome to GoalLogic AI v2.6!\n\n"
        "I analyze football matches using historical statistics, "
        "expected goals and calibrated probabilities.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )

    await update.message.reply_text(message)


async def apitest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = api_get(
            "/search",
            {"q": "Chelsea"},
        )

        if isinstance(data, list) and data:
            await update.message.reply_text(
                "🔧 FOOTBALL API TEST\n\n"
                "✅ OpenFoot API connection works.\n"
                "✅ Authentication works.\n"
                "✅ Search endpoint works.\n\n"
                "GoalLogic AI v2.6 is connected."
            )
        else:
            await update.message.reply_text(
                "⚠️ API responded, but no search data was returned."
            )

    except Exception as e:
        await update.message.reply_text(
            f"❌ API TEST FAILED\n\n{e}"
        )


async def team_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/team Chelsea"
        )
        return

    team_name = " ".join(context.args)

    try:
        team = find_team(team_name)

        message = (
            f"⚽ {team['name']}\n\n"
            f"ID: {team['id']}\n"
            f"Country: {team['country']}"
        )

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(
            f"❌ Team search failed:\n{e}"
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

    team_name = " ".join(context.args)

    try:
        team = find_team(team_name)

        matches = get_team_matches(team["id"])

        if not matches:
            await update.message.reply_text(
                f"No completed {SEASON} matches found."
            )
            return

        lines = [
            f"⚽ {team['name']} — {SEASON}",
            "",
        ]

        for match in matches[:10]:
            if match["home_id"] == team["id"]:
                opponent = match["away_name"]
                venue = "HOME"
            else:
                opponent = match["home_name"]
                venue = "AWAY"

            score = (
                f"{match['home_score']}-{match['away_score']}"
            )

            lines.append(
                f"{venue} vs {opponent} — {score}"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Fixtures failed:\n{e}"
        )


async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage:\n/analyze Chelsea vs Arsenal"
        )
        return

    full_text = " ".join(context.args)

    parts = full_text.split(" vs ")

    if len(parts) != 2:
        parts = full_text.split(" VS ")

    if len(parts) != 2:
        await update.message.reply_text(
            "Please use:\n/analyze Chelsea vs Arsenal"
        )
        return

    team1_name = parts[0].strip()
    team2_name = parts[1].strip()

    try:
        result = analyze_match(
            team1_name,
            team2_name,
        )

        team1 = result["team1"]
        team2 = result["team2"]

        team1_stats = result["team1_stats"]
        team2_stats = result["team2_stats"]

        team1_home = result["team1_home"]
        team2_away = result["team2_away"]

        team1_xg = result["team1_xg"]
        team2_xg = result["team2_xg"]

        markets = result["markets"]
        conflict = result["conflict"]
        signals = result["signals"]
        primary = result["primary"]

        lines = []

        lines.append("⚽ GOALLOGIC AI v2.6")
        lines.append("")
        lines.append(
            f"🏟️ {team1['name']} vs {team2['name']}"
        )
        lines.append("")
        lines.append(f"Season: {SEASON}")
        lines.append(
            f"Sample: Last {SAMPLE_SIZE} available matches"
        )
        lines.append("")

        lines.append(
            format_stats_line(
                team1["name"],
                team1_stats,
            )
        )

        lines.append("")

        lines.append(
            format_stats_line(
                team2["name"],
                team2_stats,
            )
        )

        lines.append("")

        lines.append(
            format_venue_line(
                team1["name"],
                "HOME",
                team1_home,
            )
        )

        lines.append(
            format_venue_line(
                team2["name"],
                "AWAY",
                team2_away,
            )
        )

        lines.append("")
        lines.append("🎯 EXPECTED GOALS")
        lines.append(
            f"{team1['name']}: {team1_xg:.2f}"
        )
        lines.append(
            f"{team2['name']}: {team2_xg:.2f}"
        )
        lines.append(
            f"Total expected goals: "
            f"{team1_xg + team2_xg:.2f}"
        )

        lines.append("")
        lines.append("📊 MARKETS")

        market_order = [
            "1X",
            "X2",
            "O1.5",
            "O2.5",
            "U3.5",
            "BTTS",
            "Team1 score",
            "Team2 score",
        ]

        for market in market_order:
            confidence = markets[market]
            icon = market_icon(confidence)
            strength = signal_strength(confidence)

            lines.append(
                f"{icon} {market}: "
                f"{confidence:.0f}% — {strength} evidence"
            )

        if conflict:
            lines.append("")
            lines.append("⚠️ RESULT MARKET CONFLICT")
            lines.append(
                "1X and X2 have closely matched support. "
                "No clear result-market edge is selected."
            )

        lines.append("")
        lines.append("🏆 TOP SIGNALS")

        if signals:
            for index, signal in enumerate(
                signals,
                start=1,
            ):
                lines.append(
                    f"{index}. {signal['market']} — "
                    f"{signal['confidence']:.0f}% "
                    f"({signal['strength']})"
                )
        else:
            lines.append(
                "No signal meets the minimum confidence threshold."
            )

        lines.append("")

        if primary:
            lines.append("🎯 PRIMARY SIGNAL")
            lines.append(
                f"{primary['market']} — "
                f"{primary['confidence']:.0f}%"
            )
        else:
            lines.append("🎯 PRIMARY SIGNAL")
            lines.append("NO CLEAR PRIMARY SIGNAL")

        lines.append("")
        lines.append(
            "📌 Advice: BET only if the available odds "
            "justify the risk."
        )

        lines.append("")
        lines.append(
            "⚠️ Statistical signal only — not a guarantee."
        )

        lines.append("")
        lines.append(
            "🤖 v2.6 Model note: Probabilities are calibrated "
            "using expected goals, historical rates, venue "
            "evidence and sample-size shrinkage. Extreme "
            "confidence is deliberately reduced when evidence "
            "is limited."
        )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ ANALYSIS FAILED\n\n{e}"
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

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("apitest", apitest)
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

    print("GoalLogic AI v2.6 Telegram bot starting...")

    application.run_polling()


if __name__ == "__main__":
    main()
