import os
import math
import asyncio
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

VERSION = "3.4"
API_BASE = "https://openfootapi.com/v1"
SEASON = "2026/27"
SAMPLE_SIZE = 5

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

# Supported competition catalog used for smarter team resolution.
COMPETITIONS = {
    "MLS": "comp_mls_usa",
    "USL Championship": "comp_usl_championship_usa",
    "Championship": "comp_championship_eng",
    "League One": "comp_league_one_eng",
    "League Two": "comp_league_two_eng",
    "Serie A": "comp_serie_a_it",
    "Serie B": "comp_serie_b_it",
    "Belgian Pro League": "comp_pro_league_be",
    "Brazil Serie A": "comp_brasileirao_bra",
    "Brazil Serie B": "comp_serie_b_bra",
}
COMPETITION_ALIASES = {
    "mls": "comp_mls_usa", "major league soccer": "comp_mls_usa",
    "championship": "comp_championship_eng", "efl championship": "comp_championship_eng",
    "serie b": "comp_serie_b_it", "italy serie b": "comp_serie_b_it",
    "serie b italy": "comp_serie_b_it", "italian serie b": "comp_serie_b_it",
    "belgium": "comp_pro_league_be", "belgian league": "comp_pro_league_be",
    "belgian pro league": "comp_pro_league_be", "pro league": "comp_pro_league_be",
    "belgian first division": "comp_pro_league_be",
    "brazil serie b": "comp_serie_b_bra", "brasileirao serie b": "comp_serie_b_bra",
}

TEAM_ALIASES = {
    "londrina": ["Londrina EC", "Londrina"],
    "criciuma": ["Criciuma EC", "Criciúma", "Criciúma EC"],
    "manchester city": ["Manchester City", "Manchester City FC"],
    "manchester united": ["Manchester United", "Manchester United FC"],
}

def normalize_name(value):
    value = str(value or "").strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    for ch in ".-_/'’":
        value = value.replace(ch, " ")
    return " ".join(value.split())

def health_response():
    return f"GoalLogic AI v{VERSION} is running."

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(health_response().encode())
    def log_message(self, format, *args):
        return

def start_health_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()

threading.Thread(target=start_health_server, daemon=True).start()

def api_get(endpoint, params=None):
    if not OPENFOOT_API_KEY:
        return None, "OPENFOOT_API_KEY is missing."
    try:
        response = requests.get(
            f"{API_BASE}{endpoint}",
            headers={"Authorization": f"Bearer {OPENFOOT_API_KEY}", "Accept": "application/json"},
            params=params or {},
            timeout=20,
        )
        if response.status_code != 200:
            return None, f"API HTTP {response.status_code}: {response.text[:300]}"
        return response.json(), None
    except Exception as e:
        return None, f"API request error: {e}"

def search_team(query):
    variants = []
    raw = str(query or "").strip()
    if raw:
        variants.append(raw)
    normalized = normalize_name(raw)
    if normalized and normalized not in [normalize_name(x) for x in variants]:
        variants.append(normalized)
    for alias in TEAM_ALIASES.get(normalized, []):
        if alias not in variants:
            variants.append(alias)

    last_error = None
    for variant in variants:
        data, error = api_get("/search", {"q": variant})
        if error:
            last_error = error
            continue
        teams = data.get("data", [])
        if isinstance(teams, list) and teams:
            return teams, None
    return [], last_error

def team_name(team):
    if not isinstance(team, dict):
        return str(team)
    return team.get("name") or team.get("shortName") or team.get("displayName") or team.get("title") or "Unknown"

def team_id(team):
    if not isinstance(team, dict):
        return None
    return team.get("id") or team.get("teamId") or team.get("team_id")

def team_country(team):
    if not isinstance(team, dict):
        return ""
    country = team.get("country")
    if isinstance(country, dict):
        return country.get("name") or ""
    return str(country or "")

def extract_teams_from_standings(data):
    rows = data.get("data", []) if isinstance(data, dict) else []
    if isinstance(rows, dict):
        rows = rows.get("standings") or rows.get("table") or rows.get("data") or []
    if not isinstance(rows, list):
        return []
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidates = [row.get("team"), row.get("club"), row.get("participant")]
        if any(isinstance(x, dict) for x in candidates):
            found.append(next(x for x in candidates if isinstance(x, dict)))
        elif row.get("name") and row.get("id"):
            found.append(row)
    return found

def competition_id_for_query(query):
    q = normalize_name(query)
    if q in COMPETITION_ALIASES:
        return COMPETITION_ALIASES[q]
    for name, cid in COMPETITIONS.items():
        if normalize_name(name) == q:
            return cid
    return None

def find_team(query):
    teams, error = search_team(query)
    q = normalize_name(query)

    if teams:
        exact = [t for t in teams if normalize_name(team_name(t)) == q]
        if exact:
            return exact[0], None
        partial = [t for t in teams if q in normalize_name(team_name(t)) or normalize_name(team_name(t)) in q]
        if partial:
            return partial[0], None
        return teams[0], None

    # For common problem cases, search the relevant competition standings.
    fallback_competitions = []
    known = TEAM_ALIASES.get(q)
    if q == "londrina" or q == "criciuma":
        fallback_competitions = ["comp_serie_b_bra"]
    else:
        # If user typed a competition-qualified query, use it.
        cid = competition_id_for_query(q)
        if cid:
            fallback_competitions = [cid]

    for cid in fallback_competitions:
        data, standings_error = api_get("/standings", {"competition": cid, "season": SEASON})
        if standings_error:
            error = standings_error
            continue
        for candidate in extract_teams_from_standings(data):
            cname = normalize_name(team_name(candidate))
            if cname == q or q in cname or cname in q:
                return candidate, None
            if known:
                for alias in known:
                    if normalize_name(alias) == cname:
                        return candidate, None

    if error:
        return None, error
    return None, f"No team found for '{query}'."

def number_value(value):
    if isinstance(value, bool): return None
    if isinstance(value, int): return value
    if isinstance(value, float): return int(value)
    if isinstance(value, str):
        try: return int(float(value.strip()))
        except Exception: return None
    return None

def extract_score_side(obj, keys):
    if not isinstance(obj, dict): return None
    for key in keys:
        if key in obj:
            value = number_value(obj.get(key))
            if value is not None: return value
    return None

def extract_scores(match):
    if not isinstance(match, dict): return None, None
    home = extract_score_side(match, ["homeScore","home_score","homeGoals","home_goals"])
    away = extract_score_side(match, ["awayScore","away_score","awayGoals","away_goals"])
    if home is not None and away is not None: return home, away
    for container_name in ("score", "scores"):
        container = match.get(container_name)
        if isinstance(container, dict):
            home = extract_score_side(container, ["home","homeScore","home_score","homeGoals"])
            away = extract_score_side(container, ["away","awayScore","away_score","awayGoals"])
            if home is not None and away is not None: return home, away
            for side, keys in (("home",["score","goals","value","current"]),("away",["score","goals","value","current"])):
                obj = container.get(side)
                value = extract_score_side(obj, keys) if isinstance(obj, dict) else None
                if side == "home": home = value if value is not None else home
                else: away = value if value is not None else away
            if home is not None and away is not None: return home, away
    return None, None

def match_kickoff(match):
    return match.get("kickoffAt") or match.get("kickoff") or match.get("date") or ""

def match_status(match):
    status = match.get("status")
    if isinstance(status, dict):
        return str(status.get("type") or status.get("name") or status.get("short") or "").lower()
    return str(status or "").lower()

def is_completed(match):
    home, away = extract_scores(match)
    if home is None or away is None: return False
    return not any(w in match_status(match) for w in ["scheduled","upcoming","pending","postponed","cancelled","canceled","live","inplay","not_started"])

def get_match_home_team(match):
    home = match.get("homeTeam")
    return team_name(home) if isinstance(home, dict) else str(home or "Unknown")

def get_match_away_team(match):
    away = match.get("awayTeam")
    return team_name(away) if isinstance(away, dict) else str(away or "Unknown")

def get_team_matches(team_id_value):
    data, error = api_get("/matches", {"team": team_id_value, "season": SEASON})
    if error: return [], error
    matches = data.get("data", [])
    if not isinstance(matches, list): return [], "Unexpected matches response."
    completed = [m for m in matches if is_completed(m)]
    completed.sort(key=match_kickoff, reverse=True)
    return completed, None

def same_team(a, b):
    return bool(a and b) and normalize_name(a) == normalize_name(b)

def team_oriented_matches(matches, target_team_id, target_team_name):
    result = []
    for match in matches:
        home_obj, away_obj = match.get("homeTeam"), match.get("awayTeam")
        home_id = team_id(home_obj) if isinstance(home_obj, dict) else None
        away_id = team_id(away_obj) if isinstance(away_obj, dict) else None
        home_name, away_name = get_match_home_team(match), get_match_away_team(match)
        if home_id == target_team_id or away_id == target_team_id or same_team(home_name,target_team_name) or same_team(away_name,target_team_name):
            hs, aas = extract_scores(match)
            if hs is None or aas is None: continue
            is_home = home_id == target_team_id or same_team(home_name,target_team_name)
            result.append({"match":match,"is_home":is_home,"gf":hs if is_home else aas,"ga":aas if is_home else hs,"total":hs+aas,"date":match_kickoff(match),"opponent":away_name if is_home else home_name})
    result.sort(key=lambda x:x["date"], reverse=True)
    return result

def recency_weights(count):
    if count <= 0: return []
    if count == 1: return [1.0]
    step = 0.40/(count-1)
    return [0.60 + step*i for i in range(count)]

def weighted_average(values):
    if not values: return 0.0
    weights = recency_weights(len(values))
    return sum(v*w for v,w in zip(values,weights))/sum(weights)

def weighted_rate(values):
    return weighted_average([1.0 if v else 0.0 for v in values])

def calculate_stats(matches):
    if not matches: return None
    wins=[]; draws=[]; losses=[]; gf=[]; ga=[]; o15=[]; o25=[]; u35=[]; btts=[]; scoring=[]; cs=[]
    for item in matches:
        f,g=item["gf"],item["ga"]; wins.append(f>g); draws.append(f==g); losses.append(f<g); gf.append(f); ga.append(g); total=f+g
        o15.append(total>=2); o25.append(total>=3); u35.append(total<=3); btts.append(f>0 and g>0); scoring.append(f>0); cs.append(g==0)
    return {"count":len(matches),"wins":sum(wins),"draws":sum(draws),"losses":sum(losses),"gf":sum(gf),"ga":sum(ga),"avg_gf":weighted_average(gf),"avg_ga":weighted_average(ga),"over15":weighted_rate(o15)*100,"over25":weighted_rate(o25)*100,"under35":weighted_rate(u35)*100,"btts":weighted_rate(btts)*100,"scoring":weighted_rate(scoring)*100,"clean_sheet":weighted_rate(cs)*100}

def form_string(matches):
    return "".join("W" if x["gf"]>x["ga"] else "D" if x["gf"]==x["ga"] else "L" for x in matches)

def get_home_matches(matches): return [x for x in matches if x["is_home"]]
def get_away_matches(matches): return [x for x in matches if not x["is_home"]]

def blend(a,b,weight_a): return a*weight_a+b*(1.0-weight_a)

def estimate_xg(team_stats,opponent_stats,venue_team,venue_opponent):
    base=team_stats["avg_gf"]*0.55+opponent_stats["avg_ga"]*0.45
    venue=venue_team["avg_gf"]*0.55+venue_opponent["avg_ga"]*0.45
    n=venue_team["count"]
    vw=0.40 if n>=5 else 0.30 if n==4 else 0.24 if n==3 else 0.18 if n==2 else 0.10 if n==1 else 0.0
    xg=blend(base,venue,vw)
    return max(0.15,min(xg*0.88+1.35*0.12,4.0))

def poisson_probability(lmbda,k):
    try: return math.exp(-lmbda)*(lmbda**k)/math.factorial(k)
    except Exception: return 0.0

def poisson_matrix(home_xg,away_xg,max_goals=8):
    matrix=[]; total=0.0
    for h in range(max_goals+1):
        row=[]
        for a in range(max_goals+1):
            p=poisson_probability(home_xg,h)*poisson_probability(away_xg,a); row.append(p); total+=p
        matrix.append(row)
    if total>0:
        matrix=[[p/total for p in row] for row in matrix]
    return matrix

def matrix_markets(matrix):
    hw=d=aw=o05=o15=o25=u35=u45=bt=hs=as_=0.0
    for h,row in enumerate(matrix):
        for a,p in enumerate(row):
            t=h+a
            if h>a: hw+=p
            elif h==a: d+=p
            else: aw+=p
            if t>=1:o05+=p
            if t>=2:o15+=p
            if t>=3:o25+=p
            if t<=3:u35+=p
            if t<=4:u45+=p
            if h>0 and a>0:bt+=p
            if h>0:hs+=p
            if a>0:as_+=p
    return {"home_win":hw*100,"draw":d*100,"away_win":aw*100,"1x":(hw+d)*100,"x2":(d+aw)*100,"over05":o05*100,"over15":o15*100,"over25":o25*100,"under35":u35*100,"under45":u45*100,"btts":bt*100,"home_score":hs*100,"away_score":as_*100}

def top_scorelines(matrix,limit=3):
    scores=[(matrix[h][a]*100,h,a) for h in range(len(matrix)) for a in range(len(matrix[h]))]
    scores.sort(reverse=True); return scores[:limit]

def grouped_goal_distribution(matrix):
    d={"zero_one":0.0,"two_three":0.0,"four":0.0,"five_plus":0.0}
    for h,row in enumerate(matrix):
        for a,p in enumerate(row):
            t=h+a
            if t<=1:d["zero_one"]+=p
            elif t<=3:d["two_three"]+=p
            elif t==4:d["four"]+=p
            else:d["five_plus"]+=p
    return {k:v*100 for k,v in d.items()}

def calculate_data_strength(hr,ar,hv,av):
    recent=(min(hr,SAMPLE_SIZE)+min(ar,SAMPLE_SIZE))/(SAMPLE_SIZE*2)
    venue=(min(hv,5)+min(av,5))/10
    return round((recent*0.65+venue*0.35)*100)

def calculate_disagreement(hr,ar,hv,av):
    diffs=[]
    for r,v in ((hr,hv),(ar,av)):
        diffs.append(abs((r["wins"]/r["count"] if r["count"] else 0)-(v["wins"]/v["count"] if v["count"] else 0)))
        diffs.append(abs(r["scoring"]-v["scoring"])/100)
    m=max(diffs)
    return "HIGH" if m>=0.60 else "MEDIUM" if m>=0.40 else "LOW"

def calculate_model_confidence(data_strength,disagreement):
    c=50+data_strength*0.32
    if disagreement=="MEDIUM":c-=5
    elif disagreement=="HIGH":c-=10
    return round(max(50,min(c,82)))

def probability_audit(m):
    return (abs(m["home_win"]+m["draw"]+m["away_win"]-100)<=0.2 and m["over05"]>=m["over15"]>=m["over25"] and m["under35"]<=m["under45"] and m["btts"]<=m["home_score"]+0.2 and m["btts"]<=m["away_score"]+0.2)

def xg_sanity_check(total_xg,over05):
    return abs((1-math.exp(-total_xg))*100-over05)<=1.0

def pct(v): return f"{round(v):.0f}%"

def format_stats(title,s,matches):
    return f"{title}:\n{form_string(matches)} | W/D/L {s['wins']}/{s['draws']}/{s['losses']} | GF{s['gf']} GA{s['ga']}\nAvg {s['avg_gf']:.2f}/{s['avg_ga']:.2f} | O1.5 {pct(s['over15'])} | O2.5 {pct(s['over25'])} | U3.5 {pct(s['under35'])} | BTTS {pct(s['btts'])}\nScoring {pct(s['scoring'])} | CS {pct(s['clean_sheet'])}"

def format_venue(title,s):
    return f"{title}:\n{s['count']} games | W/D/L {s['wins']}/{s['draws']}/{s['losses']} | Avg {s['avg_gf']:.2f}/{s['avg_ga']:.2f}\nScoring {pct(s['scoring'])} | CS {pct(s['clean_sheet'])}"

def analyze_match(home_team,away_team,home_matches,away_matches):
    hr,ar=home_matches[:SAMPLE_SIZE],away_matches[:SAMPLE_SIZE]
    hs,as_=calculate_stats(hr),calculate_stats(ar)
    if not hs or not as_: return None,"Not enough completed data."
    hvm,avm=get_home_matches(hr),get_away_matches(ar)
    hv,av=calculate_stats(hvm),calculate_stats(avm)
    if not hv: hv={"count":0,"wins":0,"draws":0,"losses":0,"gf":0,"ga":0,"avg_gf":hs["avg_gf"],"avg_ga":hs["avg_ga"],"scoring":hs["scoring"],"clean_sheet":hs["clean_sheet"]}
    if not av: av={"count":0,"wins":0,"draws":0,"losses":0,"gf":0,"ga":0,"avg_gf":as_["avg_gf"],"avg_ga":as_["avg_ga"],"scoring":as_["scoring"],"clean_sheet":as_["clean_sheet"]}
    hx=estimate_xg(hs,as_,hv,av); ax=estimate_xg(as_,hs,av,hv); tx=hx+ax
    matrix=poisson_matrix(hx,ax); m=matrix_markets(matrix); scores=top_scorelines(matrix); goals=grouped_goal_distribution(matrix)
    strength=calculate_data_strength(len(hr),len(ar),len(hvm),len(avm)); disagreement=calculate_disagreement(hs,as_,hv,av); confidence=calculate_model_confidence(strength,disagreement)
    consistency=probability_audit(m); xcheck=xg_sanity_check(tx,m["over05"])
    signals=[("Over 0.5",m["over05"]),("Over 1.5",m["over15"]),("Over 2.5",m["over25"]),("Under 3.5",m["under35"]),("Under 4.5",m["under45"]),("BTTS",m["btts"]),("Team 1 to score",m["home_score"]),("Team 2 to score",m["away_score"]),("1X",m["1x"]),("X2",m["x2"])]
    signals.sort(key=lambda x:x[1],reverse=True)
    lines=[f"⚽ GOALLOGIC AI v{VERSION}","",f"🏟️ {team_name(home_team)} vs {team_name(away_team)}","",f"Season: {SEASON}",f"Sample: Last {SAMPLE_SIZE} available matches","",format_stats(f"🏠 {team_name(home_team)} recent",hs,hr),"",format_stats(f"✈️ {team_name(away_team)} recent",as_,ar),"",format_venue(f"🏟️ {team_name(home_team)} HOME",hv),"",format_venue(f"✈️ {team_name(away_team)} AWAY",av),"","📊 EXPECTED GOALS MODEL",f"{team_name(home_team)}: {hx:.2f} xG",f"{team_name(away_team)}: {ax:.2f} xG",f"Total: {tx:.2f} xG","","🎯 RESULT MODEL",f"1: {pct(m['home_win'])} | X: {pct(m['draw'])} | 2: {pct(m['away_win'])}","","📈 MARKET MODEL",f"1X: {pct(m['1x'])}",f"X2: {pct(m['x2'])}",f"Over 0.5: {pct(m['over05'])}",f"Over 1.5: {pct(m['over15'])}",f"Over 2.5: {pct(m['over25'])}",f"Under 3.5: {pct(m['under35'])}",f"Under 4.5: {pct(m['under45'])}",f"BTTS: {pct(m['btts'])}",f"Team 1 to score: {pct(m['home_score'])}",f"Team 2 to score: {pct(m['away_score'])}","","🥅 TOP SCORELINES"]
    lines += [f"{h}-{a}: {pct(p)}" for p,h,a in scores]
    lines += ["","⚽ GOAL DISTRIBUTION",f"0-1: {pct(goals['zero_one'])} | 2-3: {pct(goals['two_three'])} | 4: {pct(goals['four'])} | 5+: {pct(goals['five_plus'])}","0-1 / 2-3 / 4 / 5+ goals","","🔥 TOP MODEL SIGNALS"]
    lines += [f"{'🟢' if p>=65 else '🟡'} {n} {pct(p)}" for n,p in signals[:3]]
    lines += ["","🧠 MODEL QUALITY",f"Data strength: {strength}%","Probability consistency: "+("✅ PASS" if consistency else "⚠️ CHECK"),"xG/market sanity check: "+("✅ PASS" if xcheck else "⚠️ CHECK"),f"Model disagreement: {disagreement}",f"Model confidence: {'🟢 HIGH' if confidence>=75 else '🟡 MODERATE' if confidence>=65 else '🔴 LOW'} {confidence}%","",f"📌 PRIMARY MODEL SIGNAL: {signals[0][0]} {pct(signals[0][1])}","","ℹ️ Probabilities are mathematical model estimates from recent data, venue evidence and a Poisson score model. They are not guarantees.","ℹ️ Model confidence measures data/model reliability; it is not the probability that a prediction will be correct."]
    return "\n".join(lines),None

async def start_command(update,context):
    await update.message.reply_text(f"⚽ Welcome to GoalLogic AI v{VERSION}!\n\nI analyze football matches using recent form, home/away evidence, weighted statistics, expected goals and a Poisson score model.\n\nCommands:\n/team Chelsea\n/fixtures Chelsea\n/analyze Chelsea vs Arsenal\n/apitest")

async def apitest_command(update,context):
    teams,error=search_team("Chelsea")
    if error: await update.message.reply_text("🔧 FOOTBALL API TEST\n\n❌ "+error); return
    if not teams: await update.message.reply_text("🔧 FOOTBALL API TEST\n\n❌ Chelsea was not found."); return
    t=teams[0]
    await update.message.reply_text(f"🔧 FOOTBALL API TEST\n\nOpenFoot API connection works.\nFound {team_name(t)}.\nTeam ID: {team_id(t)}")

async def team_command(update,context):
    q=" ".join(context.args).strip()
    if not q: await update.message.reply_text("Usage: /team Chelsea"); return
    t,error=find_team(q)
    if error: await update.message.reply_text("❌ "+error); return
    country=team_country(t)
    msg=f"🔎 TEAM SEARCH\n\nName: {team_name(t)}\nID: {team_id(t)}"
    if country: msg+=f"\nCountry: {country}"
    await update.message.reply_text(msg)

async def fixtures_command(update,context):
    q=" ".join(context.args).strip()
    if not q: await update.message.reply_text("Usage: /fixtures Chelsea"); return
    t,error=find_team(q)
    if error: await update.message.reply_text("❌ "+error); return
    matches,error=get_team_matches(team_id(t))
    if error: await update.message.reply_text("❌ "+error); return
    oriented=team_oriented_matches(matches,team_id(t),team_name(t)); recent=oriented[:SAMPLE_SIZE]
    if not recent: await update.message.reply_text(f"❌ No completed {SEASON} matches found for {team_name(t)}."); return
    lines=[f"📅 {team_name(t)}","",f"Season: {SEASON}","",f"Showing last {len(recent)} completed matches"]
    for x in recent:
        r="W" if x["gf"]>x["ga"] else "D" if x["gf"]==x["ga"] else "L"
        lines.append(f"{x['date'][:10]} | {'HOME' if x['is_home'] else 'AWAY'} | {r} | {x['gf']}-{x['ga']} vs {x['opponent']}")
    await update.message.reply_text("\n".join(lines))

async def analyze_command(update,context):
    text=" ".join(context.args).strip()
    if " vs " not in text.lower():
        await update.message.reply_text("Usage:\n/analyze Chelsea vs Arsenal"); return
    home_q,away_q=text.lower().split(" vs ",1); home_q=home_q.strip(); away_q=away_q.strip()
    if not home_q or not away_q: await update.message.reply_text("Usage:\n/analyze Chelsea vs Arsenal"); return
    ht,error=find_team(home_q)
    if error: await update.message.reply_text(f"❌ Home team error: {error}"); return
    at,error=find_team(away_q)
    if error: await update.message.reply_text(f"❌ Away team error: {error}"); return
    hm,error=get_team_matches(team_id(ht))
    if error: await update.message.reply_text(f"❌ {team_name(ht)}: {error}"); return
    am,error=get_team_matches(team_id(at))
    if error: await update.message.reply_text(f"❌ {team_name(at)}: {error}"); return
    hm=team_oriented_matches(hm,team_id(ht),team_name(ht)); am=team_oriented_matches(am,team_id(at),team_name(at))
    if not hm: await update.message.reply_text(f"❌ No recent {SEASON} data found for {team_name(ht)}."); return
    if not am: await update.message.reply_text(f"❌ No recent {SEASON} data found for {team_name(at)}."); return
    result,error=analyze_match(ht,at,hm,am)
    if error: await update.message.reply_text("❌ Analysis error: "+error); return
    await update.message.reply_text(result)

async def start_telegram_bot():
    print(f"🔵 GoalLogic AI v{VERSION} Telegram startup beginning...",flush=True)
    if not TELEGRAM_BOT_TOKEN: raise RuntimeError("❌ TELEGRAM_BOT_TOKEN is missing.")
    application=Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    for cmd,fn in [("start",start_command),("apitest",apitest_command),("team",team_command),("fixtures",fixtures_command),("analyze",analyze_command)]:
        application.add_handler(CommandHandler(cmd,fn))
    print("🔵 Telegram handlers loaded.",flush=True)
    await application.initialize()
    try:
        bot_info=await application.bot.get_me()
        print(f"✅ Telegram connection successful: @{bot_info.username}",flush=True)
        await application.bot.delete_webhook(drop_pending_updates=True)
        print("✅ Telegram webhook cleared.",flush=True)
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        print(f"🟢 GoalLogic AI v{VERSION} Telegram polling started.",flush=True)
        print("🟢 Bot is now listening for commands.",flush=True)
        await asyncio.Event().wait()
    except Exception as e:
        print(f"❌ TELEGRAM STARTUP ERROR: {type(e).__name__}: {e}",flush=True)
        raise
    finally:
        try: await application.updater.stop()
        except Exception: pass
        try: await application.stop()
        except Exception: pass
        try: await application.shutdown()
        except Exception: pass

def main():
    print(f"⚽ GoalLogic AI v{VERSION} Telegram bot is starting.",flush=True)
    asyncio.run(start_telegram_bot())

if __name__=="__main__":
    main()
