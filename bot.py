import os
import threading
import statistics
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# GOALLOGIC AI v2.5
# Conflict-Aware Analysis Engine
# ============================================================

API_BASE = "https://openfootapi.com"
SEASON = "2026/27"
RECENT_MATCHES = 5
PORT = int(os.environ.get("PORT", "10000"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.environ.get("OPENFOOT_API_KEY")


# ============================================================
# BASIC HELPERS
# ============================================================

def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def pct(value):
    return f"{round(value)}%"


def avg(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def safe_mean(*values):
    usable = [v for v in values if v is not None]
    if not usable:
        return 0.0
    return statistics.mean(usable)


# ============================================================
# API
# ============================================================

def openfoot_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        return None, "OPENFOOT_API_KEY is missing."

    headers = {
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
            return None, f"API HTTP {response.status_code}: {response.text[:300]}"

        return response.json(), None

    except Exception as e:
        return None, f"API error: {e}"


# ============================================================
# TEAM SEARCH
# ============================================================

def search_team(team_name):
    data, error = openfoot_get(
        "/teams",
        {"search": team_name}
    )

    if error:
        return None, error

    if not data:
        return None, "No team data returned."

    if isinstance(data, dict):
        teams = data.get("data") or data.get("teams") or data.get("results") or []
    else:
        teams = data

    if not teams:
        return None, f"No team found for {team_name}."

    team = teams[0]

    return team, None


# ============================================================
# MATCH PARSING
# ============================================================

def parse_match(match):
    home_name = ""
    away_name = ""
    home_id = None
    away_id = None
    home_goals = None
    away_goals = None
    date = ""

    if not isinstance(match, dict):
        return None

    teams = match.get("teams", {})
    goals = match.get("goals", {})

    if isinstance(teams, dict):
        home = teams.get("home", {}) or {}
        away = teams.get("away", {}) or {}

        if isinstance(home, dict):
            home_name = home.get("name", "")
            home_id = home.get("id")

        if isinstance(away, dict):
            away_name = away.get("name", "")
            away_id = away.get("id")

    if isinstance(goals, dict):
        home_goals = goals.get("home")
        away_goals = goals.get("away")

    date = match.get("date", "")

    if home_goals is None or away_goals is None:
        score = match.get("score", {})

        if isinstance(score, dict):
            fulltime = score.get("fulltime", {}) or {}

            if isinstance(fulltime, dict):
                home_goals = (
                    home_goals
                    if home_goals is not None
                    else fulltime.get("home")
                )
                away_goals = (
                    away_goals
                    if away_goals is not None
                    else fulltime.get("away")
                )

    return {
        "home_name": home_name,
        "away_name": away_name,
        "home_id": home_id,
        "away_id": away_id,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "date": date
    }


# ============================================================
# RECENT MATCHES
# ============================================================

def get_recent_matches(team_id, limit=RECENT_MATCHES):
    data, error = openfoot_get(
        "/fixtures",
        {
            "team": team_id,
            "season": SEASON
        }
    )

    if error:
        return [], error

    if not data:
        return [], "No fixture data returned."

    if isinstance(data, dict):
        fixtures = data.get("data") or data.get("fixtures") or data.get("results") or []
    else:
        fixtures = data

    parsed = []

    for fixture in fixtures:
        match = parse_match(fixture)

        if not match:
            continue

        if match["home_goals"] is None or match["away_goals"] is None:
            continue

        parsed.append(match)

    parsed.sort(
        key=lambda x: x.get("date", ""),
        reverse=True
    )

    return parsed[:limit], None


# ============================================================
# TEAM FIXTURES
# ============================================================

def get_team_fixtures(team_id):
    data, error = openfoot_get(
        "/fixtures",
        {
            "team": team_id,
            "season": SEASON
        }
    )

    if error:
        return [], error

    if not data:
        return [], "No fixture data returned."

    if isinstance(data, dict):
        fixtures = data.get("data") or data.get("fixtures") or data.get("results") or []
    else:
        fixtures = data

    parsed = []

    for fixture in fixtures:
        match = parse_match(fixture)

        if match:
            parsed.append(match)

    parsed.sort(
        key=lambda x: x.get("date", "")
    )

    return parsed, None


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
            "loss_rate": 0
        }

    wins = 0
    draws = 0
    losses = 0

    gf = []
    ga = []

    o15 = 0
    o25 = 0
    u35 = 0
    btts = 0
    scoring = 0
    clean_sheet = 0
    unbeaten = 0

    for match in matches:
        hg = match["home_goals"]
        ag = match["away_goals"]

        if match["home_id"] == team_id:
            team_goals = hg
            opp_goals = ag
        else:
            team_goals = ag
            opp_goals = hg

        gf.append(team_goals)
        ga.append(opp_goals)

        if team_goals > opp_goals:
            wins += 1
        elif team_goals == opp_goals:
            draws += 1
        else:
            losses += 1

        total = team_goals + opp_goals

        if total > 1.5:
            o15 += 1

        if total > 2.5:
            o25 += 1

        if total < 3.5:
            u35 += 1

        if team_goals > 0 and opp_goals > 0:
            btts += 1

        if team_goals > 0:
            scoring += 1

        if opp_goals == 0:
            clean_sheet += 1

        if team_goals >= opp_goals:
            unbeaten += 1

    count = len(matches)

    return {
        "count": count,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": sum(gf),
        "ga": sum(ga),
        "avg_gf": avg(gf),
        "avg_ga": avg(ga),
        "o15": o15 / count * 100,
        "o25": o25 / count * 100,
        "u35": u35 / count * 100,
        "btts": btts / count * 100,
        "scoring": scoring / count * 100,
        "clean_sheet": clean_sheet / count * 100,
        "unbeaten": unbeaten / count * 100,
        "loss_rate": losses / count * 100
    }


# ============================================================
# HOME / AWAY SPLIT
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
# SAMPLE RELIABILITY
# ============================================================

def sample_reliability(count):
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
# EVIDENCE AGREEMENT
# ============================================================

def evidence_agreement(values):
    usable = [
        v for v in values
        if v is not None
    ]

    if len(usable) <= 1:
        return 50.0

    spread = max(usable) - min(usable)

    agreement = 100 - (spread * 0.75)

    return clamp(agreement)


# ============================================================
# EVIDENCE STRENGTH
# ============================================================

def evidence_strength(
    overall_value,
    venue_value,
    opponent_value,
    overall_count,
    venue_count
):
    values = [
        overall_value,
        venue_value,
        opponent_value
    ]

    agreement = evidence_agreement(values)

    overall_rel = sample_reliability(overall_count)
    venue_rel = sample_reliability(venue_count)

    reliability = (overall_rel * 0.6) + (venue_rel * 0.4)

    strength = (
        agreement * 0.55
        + reliability * 0.45
    )

    return clamp(strength)


# ============================================================
# CONFLICT-AWARE MARKET
# ============================================================

def balanced_market(
    values,
    overall_count,
    venue_count,
    opponent_value=None
):
    usable = [
        v for v in values
        if v is not None
    ]

    if not usable:
        return {
            "confidence": 50,
            "strength": 0,
            "agreement": 0
        }

    raw = statistics.mean(usable)

    overall_rel = sample_reliability(overall_count)
    venue_rel = sample_reliability(venue_count)

    reliability = (
        overall_rel * 0.60
        + venue_rel * 0.40
    )

    confidence = (
        raw * reliability
        + 50 * (1 - reliability)
    )

    agreement = evidence_agreement(usable)

    if agreement >= 80:
        confidence += 3
    elif agreement < 55:
        confidence -= 5

    strength = evidence_strength(
        usable[0],
        usable[1] if len(usable) > 1 else None,
        opponent_value,
        overall_count,
        venue_count
    )

    return {
        "confidence": clamp(confidence),
        "strength": clamp(strength),
        "agreement": clamp(agreement)
    }


# ============================================================
# EXPECTED GOALS
# ============================================================

def expected_goals(
    team1_stats,
    team2_stats,
    team1_home_stats,
    team2_away_stats
):
    team1_attack = safe_mean(
        team1_stats["avg_gf"],
        team1_home_stats["avg_gf"]
    )

    team1_defense = safe_mean(
        team1_stats["avg_ga"],
        team1_home_stats["avg_ga"]
    )

    team2_attack = safe_mean(
        team2_stats["avg_gf"],
        team2_away_stats["avg_gf"]
    )

    team2_defense = safe_mean(
        team2_stats["avg_ga"],
        team2_away_stats["avg_ga"]
    )

    xg1 = (
        team1_attack * 0.55
        + team2_defense * 0.45
    )

    xg2 = (
        team2_attack * 0.55
        + team1_defense * 0.45
    )

    return max(0.1, xg1), max(0.1, xg2)


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_markets(
    team1_stats,
    team2_stats,
    team1_home,
    team2_away
):
    # --------------------------------------------------------
    # 1X
    # --------------------------------------------------------

    one_x_values = [
        team1_stats["unbeaten"],
        team1_home["unbeaten"],
        100 - team2_away["loss_rate"]
    ]

    one_x = balanced_market(
        one_x_values,
        team1_stats["count"],
        team1_home["count"],
        100 - team2_away["loss_rate"]
    )

    # --------------------------------------------------------
    # X2
    # --------------------------------------------------------

    x_two_values = [
        team2_stats["unbeaten"],
        team2_away["unbeaten"],
        100 - team1_home["loss_rate"]
    ]

    x_two = balanced_market(
        x_two_values,
        team2_stats["count"],
        team2_away["count"],
        100 - team1_home["loss_rate"]
    )

    # --------------------------------------------------------
    # O1.5
    # --------------------------------------------------------

    o15_values = [
        team1_stats["o15"],
        team2_stats["o15"],
        team1_home["o15"],
        team2_away["o15"]
    ]

    o15 = balanced_market(
        o15_values,
        min(team1_stats["count"], team2_stats["count"]),
        min(team1_home["count"], team2_away["count"])
    )

    # --------------------------------------------------------
    # O2.5
    # --------------------------------------------------------

    o25_values = [
        team1_stats["o25"],
        team2_stats["o25"],
        team1_home["o25"],
        team2_away["o25"]
    ]

    o25 = balanced_market(
        o25_values,
        min(team1_stats["count"], team2_stats["count"]),
        min(team1_home["count"], team2_away["count"])
    )

    # --------------------------------------------------------
    # U3.5
    # --------------------------------------------------------

    u35_values = [
        team1_stats["u35"],
        team2_stats["u35"],
        team1_home["u35"],
        team2_away["u35"]
    ]

    u35 = balanced_market(
        u35_values,
        min(team1_stats["count"], team2_stats["count"]),
        min(team1_home["count"], team2_away["count"])
    )

    # --------------------------------------------------------
    # BTTS
    # --------------------------------------------------------

    btts_values = [
        team1_stats["btts"],
        team2_stats["btts"],
        team1_home["btts"],
        team2_away["btts"]
    ]

    btts = balanced_market(
        btts_values,
        min(team1_stats["count"], team2_stats["count"]),
        min(team1_home["count"], team2_away["count"])
    )

    # --------------------------------------------------------
    # TEAM 1 TO SCORE
    # --------------------------------------------------------

    team1_score_values = [
        team1_stats["scoring"],
        team1_home["scoring"],
        100 - team2_away["clean_sheet"]
    ]

    team1_score = balanced_market(
        team1_score_values,
        team1_stats["count"],
        team1_home["count"],
        100 - team2_away["clean_sheet"]
    )

    # --------------------------------------------------------
    # TEAM 2 TO SCORE
    # --------------------------------------------------------

    team2_score_values = [
        team2_stats["scoring"],
        team2_away["scoring"],
        100 - team1_home["clean_sheet"]
    ]

    team2_score = balanced_market(
        team2_score_values,
        team2_stats["count"],
        team2_away["count"],
        100 - team1_home["clean_sheet"]
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

    # ========================================================
    # RESULT MARKET CONFLICT DETECTION
    # ========================================================

    result_conflict = False
    conflict_reason = ""

    one_x_conf = one_x["confidence"]
    x_two_conf = x_two["confidence"]

    difference = abs(one_x_conf - x_two_conf)

    if (
        one_x_conf >= 68
        and x_two_conf >= 68
        and difference <= 7
    ):
        result_conflict = True

        conflict_reason = (
            "1X and X2 both have strong statistical support. "
            "The result market is therefore inconclusive."
        )

    elif (
        one_x_conf >= 72
        and x_two_conf >= 72
        and difference <= 12
    ):
        result_conflict = True

        conflict_reason = (
            "1X and X2 remain closely matched. "
            "There is no clear result-market edge."
        )

    # --------------------------------------------------------
    # Conflict penalty
    # --------------------------------------------------------

    if result_conflict:
        markets["1X"]["strength"] = clamp(
            markets["1X"]["strength"] - 12
        )

        markets["X2"]["strength"] = clamp(
            markets["X2"]["strength"] - 12
        )

    return markets, result_conflict, conflict_reason


# ============================================================
# SIGNAL QUALITY
# ============================================================

def signal_label(confidence, strength):
    if confidence >= 75 and strength >= 70:
        return "Strong"

    if confidence >= 65 and strength >= 55:
        return "Moderate"

    return "Weak"


# ============================================================
# PRIMARY SIGNAL
# ============================================================

def choose_primary(markets, result_conflict):
    candidates = []

    for name, data in markets.items():
        confidence = data["confidence"]
        strength = data["strength"]

        # Do not allow conflicted result markets to become primary.
        if result_conflict and name in ("1X", "X2"):
            continue

        # Minimum quality gate.
        if confidence < 60:
            continue

        if strength < 50:
            continue

        quality = (
            confidence * 0.60
            + strength * 0.40
        )

        candidates.append(
            (
                quality,
                confidence,
                strength,
                name
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (x[0], x[1], x[2]),
        reverse=True
    )

    best = candidates[0]

    # If the best signal itself is weak, do not force a primary.
    if best[2] < 55:
        return None

    return {
        "name": best[3],
        "confidence": best[1],
        "strength": best[2]
    }


# ============================================================
# TOP SIGNALS
# ============================================================

def choose_top_signals(markets, result_conflict):
    candidates = []

    for name, data in markets.items():
        confidence = data["confidence"]
        strength = data["strength"]

        if result_conflict and name in ("1X", "X2"):
            continue

        if confidence < 60:
            continue

        if strength < 45:
            continue

        quality = (
            confidence * 0.60
            + strength * 0.40
        )

        candidates.append(
            (
                quality,
                confidence,
                strength,
                name
            )
        )

    candidates.sort(
        key=lambda x: (x[0], x[1], x[2]),
        reverse=True
    )

    return candidates[:3]


# ============================================================
# FORMAT STATS
# ============================================================

def format_form(stats):
    form = []

    # This is only used for the summary.
    # Reconstructing exact sequence is not necessary here.
    return (
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


def venue_summary(label, stats):
    return (
        f"{label} sample {stats['count']}: "
        f"W/D/L {stats['wins']}/{stats['draws']}/{stats['losses']}, "
        f"scoring {pct(stats['scoring'])}, "
        f"CS {pct(stats['clean_sheet'])}, "
        f"O2.5 {pct(stats['o25'])}, "
        f"BTTS {pct(stats['btts'])}"
    )


# ============================================================
# ANALYSIS ENGINE
# ============================================================

def run_analysis(team1_name, team2_name):
    team1, error = search_team(team1_name)

    if error:
        return f"❌ Team 1 error: {error}"

    team2, error = search_team(team2_name)

    if error:
        return f"❌ Team 2 error: {error}"

    team1_id = team1.get("id")
    team2_id = team2.get("id")

    if not team1_id or not team2_id:
        return "❌ Could not identify both teams."

    team1_matches, error = get_recent_matches(team1_id)

    if error:
        return f"❌ {team1_name} data error: {error}"

    team2_matches, error = get_recent_matches(team2_id)

    if error:
        return f"❌ {team2_name} data error: {error}"

    if not team1_matches:
        return f"❌ No recent matches found for {team1_name}."

    if not team2_matches:
        return f"❌ No recent matches found for {team2_name}."

    team1_stats = calculate_stats(
        team1_matches,
        team1_id
    )

    team2_stats = calculate_stats(
        team2_matches,
        team2_id
    )

    team1_home_matches = get_home_matches(
        team1_matches,
        team1_id
    )

    team2_away_matches = get_away_matches(
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

    xg1, xg2 = expected_goals(
        team1_stats,
        team2_stats,
        team1_home,
        team2_away
    )

    markets, result_conflict, conflict_reason = analyze_markets(
        team1_stats,
        team2_stats,
        team1_home,
        team2_away
    )

    primary = choose_primary(
        markets,
        result_conflict
    )

    top_signals = choose_top_signals(
        markets,
        result_conflict
    )

    lines = []

    lines.append("⚽ GOALLOGIC AI v2.5")
    lines.append("")
    lines.append(
        f"🏟️ {team1_name} vs {team2_name}"
    )
    lines.append("")
    lines.append(
        f"Season: {SEASON}"
    )
    lines.append(
        f"Sample: Last {RECENT_MATCHES} available matches"
    )
    lines.append("")

    lines.append(
        f"{team1_name} last {team1_stats['count']}: "
        f"{format_form(team1_stats)}"
    )

    lines.append("")

    lines.append(
        f"{team2_name} last {team2_stats['count']}: "
        f"{format_form(team2_stats)}"
    )

    lines.append("")

    lines.append(
        venue_summary(
            f"{team1_name} HOME",
            team1_home
        )
    )

    lines.append(
        venue_summary(
            f"{team2_name} AWAY",
            team2_away
        )
    )

    lines.append("")

    lines.append("🎯 EXPECTED GOALS")
    lines.append(
        f"{team1_name}: {xg1:.2f}"
    )
    lines.append(
        f"{team2_name}: {xg2:.2f}"
    )
    lines.append(
        f"Total expected goals: {xg1 + xg2:.2f}"
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
        "Team2 score"
    ]

    for name in market_order:
        data = markets[name]

        confidence = data["confidence"]
        strength = data["strength"]

        if confidence >= 70:
            icon = "🟢"
        elif confidence >= 60:
            icon = "🟡"
        else:
            icon = "🔴"

        quality = signal_label(
            confidence,
            strength
        )

        lines.append(
            f"{icon} {name}: {pct(confidence)} "
            f"— {quality} evidence"
        )

    # --------------------------------------------------------
    # RESULT MARKET STATUS
    # --------------------------------------------------------

    if result_conflict:
        lines.append("")
        lines.append("⚠️ RESULT MARKET CONFLICT")
        lines.append(
            "1X and X2 are both strongly supported, "
            "so the model does not identify a clear result-market edge."
        )

    # --------------------------------------------------------
    # TOP SIGNALS
    # --------------------------------------------------------

    lines.append("")
    lines.append("🏆 TOP SIGNALS")

    if top_signals:
        for index, item in enumerate(top_signals, start=1):
            quality, confidence, strength, name = item

            label = signal_label(
                confidence,
                strength
            )

            lines.append(
                f"{index}. {name} — {pct(confidence)} "
                f"({label})"
            )
    else:
        lines.append(
            "No signal passed the minimum evidence threshold."
        )

    # --------------------------------------------------------
    # PRIMARY
    # --------------------------------------------------------

    lines.append("")
    lines.append("🎯 PRIMARY SIGNAL")

    if primary:
        lines.append(
            f"{primary['name']} — "
            f"{pct(primary['confidence'])}"
        )
    else:
        lines.append(
            "NO CLEAR PRIMARY SIGNAL"
        )

    if result_conflict:
        lines.append("")
        lines.append(
            "📌 Result-market caution: "
            "1X/X2 evidence is conflicting."
        )

    lines.append("")
    lines.append(
        "📌 Advice: BET only if the available odds justify the risk."
    )

    lines.append("")
    lines.append(
        "⚠️ Statistical signal only — not a guarantee."
    )

    lines.append("")
    lines.append(
        "🤖 v2.5 Model note: "
        "Primary signals are filtered using confidence, "
        "evidence strength, sample reliability and "
        "result-market conflict detection."
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = (
        "⚽ Welcome to GoalLogic AI v2.5!\n\n"
        "I analyze football matches using historical statistics.\n\n"
        "Commands:\n"
        "/team Chelsea\n"
        "/fixtures Chelsea\n"
        "/analyze Chelsea vs Arsenal\n"
        "/apitest"
    )

    await update.message.reply_text(text)


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

    team, error = search_team(team_name)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    team_id = team.get("id")
    name = team.get("name", team_name)
    country = team.get("country", "")

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

    team, error = search_team(team_name)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    team_id = team.get("id")

    fixtures, error = get_team_fixtures(team_id)

    if error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    if not fixtures:
        await update.message.reply_text(
            "No fixtures found."
        )
        return

    lines = [
        f"📅 {team_name} fixtures",
        f"Season: {SEASON}",
        ""
    ]

    for fixture in fixtures[:10]:
        home = fixture["home_name"]
        away = fixture["away_name"]
        date = fixture["date"]

        lines.append(
            f"{date}\n{home} vs {away}\n"
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

    if " vs " not in text.lower():
        await update.message.reply_text(
            "Please use this format:\n"
            "/analyze Chelsea vs Arsenal"
        )
        return

    parts = text.lower().split(" vs ", 1)

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


async def apitest_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    data, error = openfoot_get(
        "/teams",
        {"search": "Chelsea"}
    )

    if error:
        await update.message.reply_text(
            f"🔧 OPENFOOT API TEST\n\n❌ {error}"
        )
        return

    await update.message.reply_text(
        "🔧 OPENFOOT API TEST\n\n"
        "✅ Football API connection works.\n"
        "✅ Authentication accepted.\n"
        "✅ Team endpoint responding."
    )


# ============================================================
# HEALTH SERVER
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

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"GoalLogic AI v2.5 is running on port {PORT}."
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
        "GoalLogic AI v2.5 Telegram bot is starting..."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
