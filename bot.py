import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from statistics import mean

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# GOALLOGIC AI v2.5
# ============================================================

API_BASE = "https://openfootapi.com/v1"
SEASON = "2026/27"
SAMPLE_SIZE = 5
PORT = int(os.environ.get("PORT", "10000"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.environ.get("OPENFOOT_API_KEY")


# ============================================================
# API
# ============================================================

def api_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        return None, "OPENFOOT_API_KEY is missing."

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {OPENFOOT_API_KEY}"
    }

    try:
        response = requests.get(
            f"{API_BASE}{endpoint}",
            headers=headers,
            params=params or {},
            timeout=20
        )

        if response.status_code != 200:
            try:
                error_data = response.json()
                message = error_data.get("error", {}).get(
                    "message",
                    response.text[:300]
                )
            except Exception:
                message = response.text[:300]

            return None, (
                f"API HTTP {response.status_code}: {message}"
            )

        payload = response.json()

        return payload.get("data", payload), None

    except Exception as exc:
        return None, f"API connection error: {exc}"


# ============================================================
# HELPERS
# ============================================================

def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def percentage(value):
    return f"{round(value)}%"


def safe_mean(values, default=0):
    values = [
        float(v)
        for v in values
        if v is not None
    ]

    if not values:
        return default

    return mean(values)


def sample_weight(count):
    if count <= 0:
        return 0.0
    if count == 1:
        return 0.35
    if count == 2:
        return 0.50
    if count == 3:
        return 0.65
    if count == 4:
        return 0.78
    return 0.88


# ============================================================
# TEAM SEARCH
# ============================================================

def find_team(team_name):
    data, error = api_get(
        "/search",
        {"q": team_name}
    )

    if error:
        return None, error

    if not data:
        return None, f"No results found for {team_name}."

    if isinstance(data, dict):
        results = data.get("results", [])
    else:
        results = data

    if not isinstance(results, list):
        results = [results]

    # Prefer an actual team result.
    team_results = []

    for item in results:
        if not isinstance(item, dict):
            continue

        item_type = str(
            item.get("type", "")
        ).lower()

        if item_type == "team":
            team_results.append(item)

    if team_results:
        return team_results[0], None

    # Fallback: find an object containing an id and name.
    for item in results:
        if isinstance(item, dict):
            if item.get("id") and item.get("name"):
                return item, None

    return None, f"No team found for {team_name}."


# ============================================================
# MATCH HELPERS
# ============================================================

def team_id_from_object(team):
    if not isinstance(team, dict):
        return None

    return team.get("id")


def team_name_from_object(team):
    if not isinstance(team, dict):
        return ""

    return team.get("name", "")


def extract_score(match):
    """
    OpenFoot can expose score information in different
    normalized/nested forms. Try the common possibilities.
    """

    possible_objects = []

    for key in (
        "score",
        "scores",
        "result",
        "fullTime",
        "fulltime"
    ):
        value = match.get(key)

        if isinstance(value, dict):
            possible_objects.append(value)

    # Direct fields.
    direct_pairs = [
        ("homeScore", "awayScore"),
        ("homeGoals", "awayGoals"),
        ("home_score", "away_score"),
        ("home", "away")
    ]

    for home_key, away_key in direct_pairs:
        if (
            home_key in match
            and away_key in match
        ):
            return (
                match.get(home_key),
                match.get(away_key)
            )

    for obj in possible_objects:

        for home_key, away_key in direct_pairs:
            if (
                home_key in obj
                and away_key in obj
            ):
                return (
                    obj.get(home_key),
                    obj.get(away_key)
                )

        # Sometimes score is nested under fulltime.
        for nested_key in (
            "fullTime",
            "fulltime",
            "current"
        ):
            nested = obj.get(nested_key)

            if isinstance(nested, dict):
                for home_key, away_key in direct_pairs:
                    if (
                        home_key in nested
                        and away_key in nested
                    ):
                        return (
                            nested.get(home_key),
                            nested.get(away_key)
                        )

    return None, None


def parse_match(match):
    if not isinstance(match, dict):
        return None

    home_team = match.get("homeTeam", {})
    away_team = match.get("awayTeam", {})

    if not isinstance(home_team, dict):
        home_team = {}

    if not isinstance(away_team, dict):
        away_team = {}

    home_id = home_team.get("id")
    away_id = away_team.get("id")

    home_name = home_team.get("name", "")
    away_name = away_team.get("name", "")

    home_goals, away_goals = extract_score(match)

    kickoff = match.get(
        "kickoffAt",
        match.get("date", "")
    )

    status = str(
        match.get("status", "")
    ).lower()

    return {
        "id": match.get("id"),
        "home_id": home_id,
        "away_id": away_id,
        "home_name": home_name,
        "away_name": away_name,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "kickoff": kickoff,
        "status": status
    }


# ============================================================
# TEAM MATCHES
# ============================================================

def get_team_matches(team_id):
    data, error = api_get(
        "/matches",
        {
            "team": team_id,
            "season": SEASON
        }
    )

    if error:
        return [], error

    if not isinstance(data, list):
        return [], "Unexpected matches response."

    matches = []

    for raw_match in data:
        match = parse_match(raw_match)

        if not match:
            continue

        # Only completed matches belong in recent-form analysis.
        if match["status"] not in (
            "finished",
            "completed",
            "fulltime",
            "ft",
            ""
        ):
            continue

        if (
            match["home_goals"] is None
            or match["away_goals"] is None
        ):
            continue

        matches.append(match)

    matches.sort(
        key=lambda x: x.get("kickoff", ""),
        reverse=True
    )

    return matches[:SAMPLE_SIZE], None


def get_all_team_matches(team_id):
    data, error = api_get(
        "/matches",
        {
            "team": team_id,
            "season": SEASON
        }
    )

    if error:
        return [], error

    if not isinstance(data, list):
        return [], "Unexpected matches response."

    matches = []

    for raw_match in data:
        match = parse_match(raw_match)

        if not match:
            continue

        if match["home_goals"] is None:
            continue

        if match["away_goals"] is None:
            continue

        matches.append(match)

    matches.sort(
        key=lambda x: x.get("kickoff", "")
    )

    return matches, None


# ============================================================
# STATS
# ============================================================

def calculate_stats(matches, team_id):
    if not matches:
        return {
            "count": 0,
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
            "form": ""
        }

    wins = 0
    draws = 0
    losses = 0

    gf_total = 0
    ga_total = 0

    o15 = 0
    o25 = 0
    u35 = 0
    btts = 0
    scoring = 0
    clean_sheet = 0
    unbeaten = 0

    form = []

    for match in matches:

        if match["home_id"] == team_id:
            gf = match["home_goals"]
            ga = match["away_goals"]
        else:
            gf = match["away_goals"]
            ga = match["home_goals"]

        gf = float(gf)
        ga = float(ga)

        gf_total += gf
        ga_total += ga

        total = gf + ga

        if gf > ga:
            wins += 1
            form.append("W")
        elif gf == ga:
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

        if gf > 0 and ga > 0:
            btts += 1

        if gf > 0:
            scoring += 1

        if ga == 0:
            clean_sheet += 1

        if gf >= ga:
            unbeaten += 1

    count = len(matches)

    return {
        "count": count,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf_total,
        "ga": ga_total,
        "avg_gf": gf_total / count,
        "avg_ga": ga_total / count,
        "o15": o15 / count * 100,
        "o25": o25 / count * 100,
        "u35": u35 / count * 100,
        "btts": btts / count * 100,
        "scoring": scoring / count * 100,
        "clean_sheet": clean_sheet / count * 100,
        "unbeaten": unbeaten / count * 100,
        "loss_rate": losses / count * 100,
        "form": "".join(form)
    }


def home_matches(matches, team_id):
    return [
        m for m in matches
        if m["home_id"] == team_id
    ]


def away_matches(matches, team_id):
    return [
        m for m in matches
        if m["away_id"] == team_id
    ]


# ============================================================
# EVIDENCE
# ============================================================

def agreement(values):
    values = [
        v for v in values
        if v is not None
    ]

    if len(values) < 2:
        return 50

    spread = max(values) - min(values)

    return clamp(
        100 - spread * 0.75
    )


def market_score(values, sample_count):
    values = [
        v for v in values
        if v is not None
    ]

    if not values:
        return {
            "confidence": 50,
            "strength": 0
        }

    raw = safe_mean(values)

    reliability = sample_weight(
        sample_count
    )

    confidence = (
        raw * reliability
        + 50 * (1 - reliability)
    )

    agree = agreement(values)

    if agree >= 80:
        confidence += 3
    elif agree < 55:
        confidence -= 5

    strength = (
        agree * 0.55
        + reliability * 100 * 0.45
    )

    return {
        "confidence": clamp(confidence),
        "strength": clamp(strength)
    }


def quality_label(confidence, strength):
    if confidence >= 75 and strength >= 70:
        return "Strong"

    if confidence >= 65 and strength >= 55:
        return "Moderate"

    return "Weak"


# ============================================================
# EXPECTED GOALS
# ============================================================

def calculate_xg(
    team1,
    team2,
    team1_home,
    team2_away
):
    team1_attack = safe_mean([
        team1["avg_gf"],
        team1_home["avg_gf"]
    ])

    team1_defence = safe_mean([
        team1["avg_ga"],
        team1_home["avg_ga"]
    ])

    team2_attack = safe_mean([
        team2["avg_gf"],
        team2_away["avg_gf"]
    ])

    team2_defence = safe_mean([
        team2["avg_ga"],
        team2_away["avg_ga"]
    ])

    xg1 = (
        team1_attack * 0.55
        + team2_defence * 0.45
    )

    xg2 = (
        team2_attack * 0.55
        + team1_defence * 0.45
    )

    return (
        max(0.1, xg1),
        max(0.1, xg2)
    )


# ============================================================
# MARKET ENGINE
# ============================================================

def analyze_markets(
    team1,
    team2,
    team1_home,
    team2_away
):
    min_count = min(
        team1["count"],
        team2["count"]
    )

    venue_count = min(
        team1_home["count"],
        team2_away["count"]
    )

    # --------------------------------------------------------
    # RESULT MARKETS
    # --------------------------------------------------------

    one_x = market_score(
        [
            team1["unbeaten"],
            team1_home["unbeaten"],
            100 - team2_away["loss_rate"]
        ],
        min_count
    )

    x_two = market_score(
        [
            team2["unbeaten"],
            team2_away["unbeaten"],
            100 - team1_home["loss_rate"]
        ],
        min_count
    )

    # --------------------------------------------------------
    # GOALS
    # --------------------------------------------------------

    o15 = market_score(
        [
            team1["o15"],
            team2["o15"],
            team1_home["o15"],
            team2_away["o15"]
        ],
        min_count
    )

    o25 = market_score(
        [
            team1["o25"],
            team2["o25"],
            team1_home["o25"],
            team2_away["o25"]
        ],
        min_count
    )

    u35 = market_score(
        [
            team1["u35"],
            team2["u35"],
            team1_home["u35"],
            team2_away["u35"]
        ],
        min_count
    )

    btts = market_score(
        [
            team1["btts"],
            team2["btts"],
            team1_home["btts"],
            team2_away["btts"]
        ],
        min_count
    )

    team1_score = market_score(
        [
            team1["scoring"],
            team1_home["scoring"],
            100 - team2_away["clean_sheet"]
        ],
        min_count
    )

    team2_score = market_score(
        [
            team2["scoring"],
            team2_away["scoring"],
            100 - team1_home["clean_sheet"]
        ],
        min_count
    )

    markets = {
        "1X": one_x,
        "X2": x_two,
        "O1.5": o15,
        "O2.5": o25,
        "U3.5": u35,
        "BTTS": btts,
        "Team1 score": team1_score,
        "Team2 score": team2_score
    }

    # --------------------------------------------------------
    # 1X / X2 CONFLICT
    # --------------------------------------------------------

    result_conflict = False

    if (
        one_x["confidence"] >= 68
        and x_two["confidence"] >= 68
        and abs(
            one_x["confidence"]
            - x_two["confidence"]
        ) <= 7
    ):
        result_conflict = True

    if result_conflict:
        markets["1X"]["strength"] = clamp(
            markets["1X"]["strength"] - 15
        )

        markets["X2"]["strength"] = clamp(
            markets["X2"]["strength"] - 15
        )

    return markets, result_conflict


# ============================================================
# PRIMARY SIGNAL
# ============================================================

def choose_primary(markets, result_conflict):
    candidates = []

    for name, data in markets.items():

        if (
            result_conflict
            and name in ("1X", "X2")
        ):
            continue

        confidence = data["confidence"]
        strength = data["strength"]

        if confidence < 60:
            continue

        if strength < 50:
            continue

        quality = (
            confidence * 0.60
            + strength * 0.40
        )

        candidates.append({
            "name": name,
            "confidence": confidence,
            "strength": strength,
            "quality": quality
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["quality"],
        reverse=True
    )

    best = candidates[0]

    if best["strength"] < 55:
        return None

    return best


def top_signals(markets, result_conflict):
    candidates = []

    for name, data in markets.items():

        if (
            result_conflict
            and name in ("1X", "X2")
        ):
            continue

        if data["confidence"] < 60:
            continue

        if data["strength"] < 45:
            continue

        quality = (
            data["confidence"] * 0.60
            + data["strength"] * 0.40
        )

        candidates.append({
            "name": name,
            "confidence": data["confidence"],
            "strength": data["strength"],
            "quality": quality
        })

    candidates.sort(
        key=lambda x: x["quality"],
        reverse=True
    )

    return candidates[:3]


# ============================================================
# ANALYSIS
# ============================================================

def run_analysis(team1_name, team2_name):
    team1, error = find_team(team1_name)

    if error:
        return f"❌ Team 1 error: {error}"

    team2, error = find_team(team2_name)

    if error:
        return f"❌ Team 2 error: {error}"

    team1_id = team_id_from_object(team1)
    team2_id = team_id_from_object(team2)

    if not team1_id or not team2_id:
        return "❌ Could not identify both teams."

    team1_matches, error = get_team_matches(
        team1_id
    )

    if error:
        return (
            f"❌ {team1_name} match data error: "
            f"{error}"
        )

    team2_matches, error = get_team_matches(
        team2_id
    )

    if error:
        return (
            f"❌ {team2_name} match data error: "
            f"{error}"
        )

    if not team1_matches:
        return (
            f"❌ No completed {SEASON} matches "
            f"found for {team1_name}."
        )

    if not team2_matches:
        return (
            f"❌ No completed {SEASON} matches "
            f"found for {team2_name}."
        )

    team1_stats = calculate_stats(
        team1_matches,
        team1_id
    )

    team2_stats = calculate_stats(
        team2_matches,
        team2_id
    )

    team1_home_matches = home_matches(
        team1_matches,
        team1_id
    )

    team2_away_matches = away_matches(
        team2_matches,
        team2_id
    )

    team1_home = calculate_stats(
        team1_home_matches,
        team1_id
    )

    team2_away = calculate_stats(
        team2_away_matches,
        team2_id
    )

    xg1, xg2 = calculate_xg(
        team1_stats,
        team2_stats,
        team1_home,
        team2_away
    )

    markets, result_conflict = analyze_markets(
        team1_stats,
        team2_stats,
        team1_home,
        team2_away
    )

    primary = choose_primary(
        markets,
        result_conflict
    )

    signals = top_signals(
        markets,
        result_conflict
    )

    lines = []

    lines.append("⚽ GOALLOGIC AI v2.5")
    lines.append("")
    lines.append(
        f"🏟️ {team1_name.title()} vs "
        f"{team2_name.title()}"
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
        f"{team1_name.title()} last "
        f"{team1_stats['count']}: "
        f"{team1_stats['form']}, "
        f"W/D/L "
        f"{team1_stats['wins']}/"
        f"{team1_stats['draws']}/"
        f"{team1_stats['losses']}, "
        f"GF{int(team1_stats['gf'])} "
        f"GA{int(team1_stats['ga'])}, "
        f"avg "
        f"{team1_stats['avg_gf']:.2f}/"
        f"{team1_stats['avg_ga']:.2f}, "
        f"O1.5 {percentage(team1_stats['o15'])}, "
        f"O2.5 {percentage(team1_stats['o25'])}, "
        f"U3.5 {percentage(team1_stats['u35'])}, "
        f"BTTS {percentage(team1_stats['btts'])}, "
        f"scoring {percentage(team1_stats['scoring'])}, "
        f"CS {percentage(team1_stats['clean_sheet'])}"
    )

    lines.append("")

    lines.append(
        f"{team2_name.title()} last "
        f"{team2_stats['count']}: "
        f"{team2_stats['form']}, "
        f"W/D/L "
        f"{team2_stats['wins']}/"
        f"{team2_stats['draws']}/"
        f"{team2_stats['losses']}, "
        f"GF{int(team2_stats['gf'])} "
        f"GA{int(team2_stats['ga'])}, "
        f"avg "
        f"{team2_stats['avg_gf']:.2f}/"
        f"{team2_stats['avg_ga']:.2f}, "
        f"O1.5 {percentage(team2_stats['o15'])}, "
        f"O2.5 {percentage(team2_stats['o25'])}, "
        f"U3.5 {percentage(team2_stats['u35'])}, "
        f"BTTS {percentage(team2_stats['btts'])}, "
        f"scoring {percentage(team2_stats['scoring'])}, "
        f"CS {percentage(team2_stats['clean_sheet'])}"
    )

    lines.append("")

    lines.append(
        f"{team1_name.title()} HOME sample "
        f"{team1_home['count']}: "
        f"W/D/L "
        f"{team1_home['wins']}/"
        f"{team1_home['draws']}/"
        f"{team1_home['losses']}, "
        f"scoring {percentage(team1_home['scoring'])}, "
        f"CS {percentage(team1_home['clean_sheet'])}, "
        f"O2.5 {percentage(team1_home['o25'])}, "
        f"BTTS {percentage(team1_home['btts'])}"
    )

    lines.append(
        f"{team2_name.title()} AWAY sample "
        f"{team2_away['count']}: "
        f"W/D/L "
        f"{team2_away['wins']}/"
        f"{team2_away['draws']}/"
        f"{team2_away['losses']}, "
        f"scoring {percentage(team2_away['scoring'])}, "
        f"CS {percentage(team2_away['clean_sheet'])}, "
        f"O2.5 {percentage(team2_away['o25'])}, "
        f"BTTS {percentage(team2_away['btts'])}"
    )

    lines.append("")
    lines.append("🎯 EXPECTED GOALS")
    lines.append(
        f"{team1_name.title()}: {xg1:.2f}"
    )
    lines.append(
        f"{team2_name.title()}: {xg2:.2f}"
    )
    lines.append(
        f"Total expected goals: {xg1 + xg2:.2f}"
    )

    lines.append("")
    lines.append("📊 MARKETS")

    market_names = [
        "1X",
        "X2",
        "O1.5",
        "O2.5",
        "U3.5",
        "BTTS",
        "Team1 score",
        "Team2 score"
    ]

    for name in market_names:
        data = markets[name]

        confidence = data["confidence"]
        strength = data["strength"]

        if confidence >= 70:
            icon = "🟢"
        elif confidence >= 60:
            icon = "🟡"
        else:
            icon = "🔴"

        label = quality_label(
            confidence,
            strength
        )

        lines.append(
            f"{icon} {name}: "
            f"{percentage(confidence)} "
            f"— {label} evidence"
        )

    if result_conflict:
        lines.append("")
        lines.append("⚠️ RESULT MARKET CONFLICT")
        lines.append(
            "1X and X2 have closely matched "
            "strong support. No clear result-market "
            "edge is selected."
        )

    lines.append("")
    lines.append("🏆 TOP SIGNALS")

    if signals:
        for number, signal in enumerate(
            signals,
            start=1
        ):
            label = quality_label(
                signal["confidence"],
                signal["strength"]
            )

            lines.append(
                f"{number}. "
                f"{signal['name']} — "
                f"{percentage(signal['confidence'])} "
                f"({label})"
            )
    else:
        lines.append(
            "No signal passed the evidence threshold."
        )

    lines.append("")
    lines.append("🎯 PRIMARY SIGNAL")

    if primary:
        lines.append(
            f"{primary['name']} — "
            f"{percentage(primary['confidence'])}"
        )
    else:
        lines.append(
            "NO CLEAR PRIMARY SIGNAL"
        )

    lines.append("")
    lines.append(
        "📌 Advice: BET only if the available "
        "odds justify the risk."
    )

    lines.append("")
    lines.append(
        "⚠️ Statistical signal only — not a guarantee."
    )

    lines.append("")
    lines.append(
        "🤖 v2.5 Model note: Confidence is adjusted "
        "for sample size, venue evidence, agreement "
        "between indicators and result-market conflict."
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "⚽ Welcome to GoalLogic AI v2.5!\n\n"
        "I analyze football matches using "
        "historical statistics.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )


async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    data, error = api_get(
        "/health"
    )

    if error:
        await update.message.reply_text(
            "🔧 OPENFOOT API TEST\n\n"
            f"❌ {error}"
        )
        return

    await update.message.reply_text(
        "🔧 OPENFOOT API TEST\n\n"
        "✅ OpenFoot API connection works.\n"
        "✅ API v1 endpoint responding.\n"
        "✅ Authentication accepted."
    )


async def team_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.message.reply_text(
            "Usage: /team Chelsea"
        )
        return

    team_name = " ".join(context.args)

    team, error = find_team(team_name)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    name = team.get(
        "name",
        team_name
    )

    team_id = team.get(
        "id",
        "Unknown"
    )

    country = team.get(
        "country",
        ""
    )

    await update.message.reply_text(
        f"⚽ {name}\n"
        f"ID: {team_id}\n"
        f"Country: {country}"
    )


async def fixtures_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.message.reply_text(
            "Usage: /fixtures Chelsea"
        )
        return

    team_name = " ".join(context.args)

    team, error = find_team(team_name)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    team_id = team.get("id")

    data, error = api_get(
        "/matches",
        {
            "team": team_id,
            "season": SEASON
        }
    )

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    if not isinstance(data, list):
        await update.message.reply_text(
            "❌ Unexpected matches response."
        )
        return

    matches = []

    for raw_match in data:
        match = parse_match(raw_match)

        if match:
            matches.append(match)

    matches.sort(
        key=lambda x: x.get("kickoff", "")
    )

    if not matches:
        await update.message.reply_text(
            f"No {SEASON} fixtures found."
        )
        return

    lines = [
        f"📅 {team_name.title()} fixtures",
        f"Season: {SEASON}",
        ""
    ]

    for match in matches[:10]:
        kickoff = match["kickoff"]
        home = match["home_name"]
        away = match["away_name"]

        lines.append(
            f"{kickoff}\n"
            f"{home} vs {away}\n"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.message.reply_text(
            "Usage: /analyze Chelsea vs Arsenal"
        )
        return

    text = " ".join(context.args)

    lower_text = text.lower()

    if " vs " not in lower_text:
        await update.message.reply_text(
            "Please use:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    parts = lower_text.split(
        " vs ",
        1
    )

    team1 = parts[0].strip()
    team2 = parts[1].strip()

    if not team1 or not team2:
        await update.message.reply_text(
            "Please provide two teams."
        )
        return

    await update.message.reply_text(
        "🔎 Analyzing the match..."
    )

    result = run_analysis(
        team1,
        team2
    )

    await update.message.reply_text(
        result
    )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain"
        )
        self.end_headers()

        self.wfile.write(
            b"GoalLogic AI v2.5 is running."
        )

    def log_message(
        self,
        format,
        *args
    ):
        return


def start_health_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"GoalLogic AI v2.5 is running "
        f"on port {PORT}."
    )

    server.serve_forever()


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
        daemon=True
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
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "apitest",
            apitest_command
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

    print(
        "GoalLogic AI v2.5 Telegram bot "
        "is starting..."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
