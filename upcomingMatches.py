import streamlit as st
import requests
import time
from dataclasses import dataclass, field

# --- Configuration ---
# NOTE: Season is the year the season STARTS (e.g., 2024 for 2024/25 season)
SEASON = 2025

# football-data.org API
API_BASE = "https://api.football-data.org/v4"
API_KEY = "f1cc4ca1173d4528b60411d86ad12b2c"

# Available leagues (football-data.org free tier)
LEAGUES = {
    "🇩🇪 Bundesliga": {"code": "BL1", "teams": 18},
    "🇩🇪 2. Bundesliga": {"code": "BL2", "teams": 18},
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League": {"code": "PL", "teams": 20},
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Championship": {"code": "ELC", "teams": 24},
    "🇪🇸 La Liga": {"code": "PD", "teams": 20},
    "🇪🇸 Segunda División": {"code": "SD", "teams": 22},
    "🇮🇹 Serie A": {"code": "SA", "teams": 20},
    "🇮🇹 Serie B": {"code": "SB", "teams": 20},
    "🇫🇷 Ligue 1": {"code": "FL1", "teams": 18},
    "🇫🇷 Ligue 2": {"code": "FL2", "teams": 18},
}

# Label thresholds (some will be calculated dynamically based on league size)
TITLE_RACE_MAX_POS = 8
RELEGATION_MIN_POS = 13  # Base for 18-team leagues, adjusted dynamically for larger leagues
CLOSE_MATCH_DISTANCE = 5  # Position distance for title race / relegation
CLOSE_MATCH_POINTS = 7  # Points distance for head-to-head label
UPSET_DISTANCE = 10  # Position distance for upset potential

# Rate limiting: free tier = 10 requests/minute
REQUEST_DELAY = 6.5  # seconds between requests to stay under limit


@dataclass
class FormMatch:
    """Single match result for form display."""
    result: str  # W, D, L
    opponent: str
    goals_for: int
    goals_against: int


@dataclass
class Team:
    id: int
    name: str
    short_name: str
    position: int
    points: int
    num_teams: int  # Total teams in league for dynamic calculations
    crest_url: str = ""
    form: list[FormMatch] = field(default_factory=list)

    @property
    def buzz_score(self) -> int:
        """Higher table position = higher buzz score"""
        return self.num_teams - self.position

    @property
    def form_display(self) -> str:
        """Display form as emoji string (oldest left, newest right)."""
        symbols = {"W": "🟢", "D": "🟡", "L": "🔴"}
        return " ".join(symbols.get(fm.result, "⚪") for fm in self.form) if self.form else "—"

    def form_display_html(self) -> str:
        """Display form with hover tooltips (oldest left, newest right)."""
        if not self.form:
            return "—"

        symbols = {"W": "🟢", "D": "🟡", "L": "🔴"}
        parts = []

        for fm in self.form:
            symbol = symbols.get(fm.result, "⚪")
            tooltip = f"{fm.goals_for}:{fm.goals_against} vs {fm.opponent}"
            parts.append(f'<span title="{tooltip}" style="cursor:help;">{symbol}</span>')

        return " ".join(parts)


@dataclass
class Match:
    home: Team
    away: Team
    kickoff: str
    matchday: int
    is_finished: bool = False
    home_score: int = 0
    away_score: int = 0

    @property
    def num_teams(self) -> int:
        """Get number of teams in the league from home team."""
        return self.home.num_teams

    @property
    def base_score(self) -> int:
        return self.home.buzz_score + self.away.buzz_score

    @property
    def position_distance(self) -> int:
        return abs(self.home.position - self.away.position)

    @property
    def points_distance(self) -> int:
        return abs(self.home.points - self.away.points)

    @property
    def closeness_bonus(self) -> float:
        """Teams closer in table = higher bonus (1.0 to ~2.0)"""
        max_distance = self.num_teams - 1
        return 1 + (max_distance - self.position_distance) / max_distance

    @property
    def max_buzz(self) -> float:
        """Maximum possible buzz score (1st vs 1st)."""
        # base = (num_teams - 1) + (num_teams - 1) = 2 * (num_teams - 1)
        # closeness = 2 (distance = 0)
        return 4 * (self.num_teams - 1)

    @property
    def buzz_raw(self) -> float:
        """Raw buzz score before normalization."""
        return self.base_score * self.closeness_bonus

    @property
    def buzz(self) -> float:
        """Normalized buzz score (0-100 scale)."""
        return (self.buzz_raw / self.max_buzz) * 100

    @property
    def titan_slayed(self) -> bool:
        """Check if the underdog won (upset actually happened)."""
        if not self.is_finished:
            return False

        # Determine favorite (lower position = better)
        if self.home.position < self.away.position:
            favorite_score, underdog_score = self.home_score, self.away_score
        else:
            favorite_score, underdog_score = self.away_score, self.home_score

        # Underdog won and it was a potential upset match
        return underdog_score > favorite_score and self.position_distance >= UPSET_DISTANCE

    @property
    def relegation_min_pos(self) -> int:
        """Dynamic relegation zone start based on league size.
        Base is RELEGATION_MIN_POS (13) for 18-team leagues,
        adjusted by (num_teams - 18) for larger leagues.
        """
        return RELEGATION_MIN_POS + (self.num_teams - 18)

    @property
    def labels(self) -> list[str]:
        labels = []
        highest_pos = min(self.home.position, self.away.position)
        lowest_pos = max(self.home.position, self.away.position)

        # TITAN SLAYED: upset actually happened
        if self.titan_slayed:
            labels.append("⚔️ TITAN SLAYED")

        # PRIMARY LABELS (based on table position)
        # Title Race: both teams near the top and close in standings
        if lowest_pos <= TITLE_RACE_MAX_POS and self.position_distance <= CLOSE_MATCH_DISTANCE:
            labels.append("🏆 Title Race")

        # Relegation Battle: both teams near the bottom and close in standings
        if highest_pos >= self.relegation_min_pos and self.position_distance <= CLOSE_MATCH_DISTANCE:
            labels.append("🔥 Relegation Battle")

        # SECONDARY LABELS
        # Head-to-Head: close in points (regardless of position)
        if self.points_distance <= CLOSE_MATCH_POINTS:
            labels.append("🎯 Head-to-Head")

        # Upset Potential: big gap in table positions
        if self.position_distance >= UPSET_DISTANCE:
            labels.append("⚡ Upset Potential")

        return labels


def api_request(endpoint: str, params: dict = None) -> dict:
    """Make authenticated request to football-data.org API."""
    headers = {"X-Auth-Token": API_KEY}
    url = f"{API_BASE}{endpoint}"

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code == 429:
            # Rate limited - wait and retry (silently, UI handled elsewhere)
            retry_after = int(response.headers.get("X-RequestCounter-Reset", 60))
            time.sleep(retry_after)
            response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code != 200:
            error_msg = response.json().get("message", response.text)
            raise Exception(f"API Error {response.status_code}: {error_msg}")

        return response.json()

    except requests.exceptions.Timeout:
        raise Exception(f"Timeout bei {endpoint}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Request Fehler: {e}")


@st.cache_data(ttl=6000)  # Cache for ~100 minutes
def fetch_standings_cached(competition: str) -> list[dict]:
    """Fetch current standings (cached)."""
    data = api_request(f"/competitions/{competition}/standings", {"season": SEASON})
    return data["standings"][0]["table"]


def fetch_standings(competition: str, num_teams: int) -> dict[int, Team]:
    """Build Team dict from cached standings."""
    standings = fetch_standings_cached(competition)

    teams = {}
    for entry in standings:
        team_data = entry["team"]
        team_id = team_data["id"]

        teams[team_id] = Team(
            id=team_id,
            name=team_data["name"],
            short_name=team_data.get("shortName", team_data["name"]),
            position=entry["position"],
            points=entry["points"],
            num_teams=num_teams,
            crest_url=team_data.get("crest", "")
        )

    return teams


@st.cache_data(ttl=6000)
def fetch_current_matchday(competition: str) -> int:
    """Get the current matchday number (cached)."""
    data = api_request(f"/competitions/{competition}", {"season": SEASON})
    return data["currentSeason"]["currentMatchday"]


@st.cache_data(ttl=6000)
def fetch_matches_cached(competition: str, matchday: int) -> list[dict]:
    """Fetch matches for a specific matchday (cached)."""
    data = api_request(
        f"/competitions/{competition}/matches",
        {"season": SEASON, "matchday": matchday}
    )
    return data.get("matches", [])


def fetch_matches(competition: str, matchday: int, teams: dict[int, Team]) -> list[Match]:
    """Fetch matches for a specific matchday."""
    matches_data = fetch_matches_cached(competition, matchday)

    matches = []
    for m in matches_data:
        home_id = m["homeTeam"]["id"]
        away_id = m["awayTeam"]["id"]

        # Skip if team not in standings (shouldn't happen)
        if home_id not in teams or away_id not in teams:
            continue

        # Check if match is finished and get score
        is_finished = m.get("status") == "FINISHED"
        home_score = 0
        away_score = 0

        if is_finished:
            score = m.get("score", {}).get("fullTime", {})
            home_score = score.get("home", 0) or 0
            away_score = score.get("away", 0) or 0

        matches.append(Match(
            home=teams[home_id],
            away=teams[away_id],
            kickoff=m.get("utcDate", "TBD"),
            matchday=matchday,
            is_finished=is_finished,
            home_score=home_score,
            away_score=away_score
        ))

    return sorted(matches, key=lambda x: x.buzz, reverse=True)


@st.cache_data(ttl=6000, show_spinner=False)  # Cache form for ~100 minutes
def fetch_team_form_cached(team_id: int) -> tuple[list[dict], str]:
    """Fetch last 5 match results for a team (all competitions). Returns (form_data, timestamp)."""
    # Rate limit delay - only executes when not cached
    time.sleep(REQUEST_DELAY)

    from datetime import datetime
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")

    try:
        data = api_request(
            f"/teams/{team_id}/matches",
            {"status": "FINISHED", "limit": 5}
        )

        form = []
        for match in data.get("matches", [])[:5]:
            home_id = match["homeTeam"]["id"]
            home_name = match["homeTeam"].get("shortName", match["homeTeam"]["name"])
            away_name = match["awayTeam"].get("shortName", match["awayTeam"]["name"])
            home_goals = match["score"]["fullTime"]["home"]
            away_goals = match["score"]["fullTime"]["away"]

            if home_goals is None or away_goals is None:
                continue

            is_home = (home_id == team_id)
            opponent = away_name if is_home else home_name
            goals_for = home_goals if is_home else away_goals
            goals_against = away_goals if is_home else home_goals

            if goals_for > goals_against:
                result = "W"
            elif goals_for < goals_against:
                result = "L"
            else:
                result = "D"

            form.append({
                "result": result,
                "opponent": opponent,
                "goals_for": goals_for,
                "goals_against": goals_against
            })

        return form, timestamp

    except Exception:
        return [], timestamp


def compute_team_form(teams: dict[int, Team], progress_bar=None) -> str:
    """Fetch last 5 match results for each team (all competitions). Returns last fetch timestamp."""
    team_list = list(teams.items())
    last_timestamp = ""

    for i, (team_id, team) in enumerate(team_list):
        form_data, timestamp = fetch_team_form_cached(team_id)
        team.form = [FormMatch(**fm) for fm in form_data]
        last_timestamp = timestamp

        if progress_bar:
            progress_bar.progress((i + 1) / len(team_list), f"Lade Form: {team.short_name}")

    return last_timestamp


def format_kickoff(iso_datetime: str) -> str:
    """Format ISO datetime to readable format in German timezone."""
    if iso_datetime == "TBD":
        return "TBD"
    try:
        from datetime import datetime, timezone, timedelta
        from zoneinfo import ZoneInfo

        # Parse UTC time
        dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
        # Convert to German timezone (handles DST automatically)
        dt_german = dt.astimezone(ZoneInfo("Europe/Berlin"))
        return dt_german.strftime("%a %d.%m. %H:%M")
    except:
        return iso_datetime


# --- Streamlit UI ---
st.set_page_config(page_title="Football Buzz", page_icon="⚽", layout="wide")

# Mobile-responsive CSS
st.markdown("""
<style>
    /* Mobile adjustments */
    @media (max-width: 640px) {
        img {
            max-width: 28px !important;
        }
        h2 {
            font-size: 1.2rem !important;
        }
        .stMarkdown small {
            font-size: 0.7rem !important;
        }
    }

    /* Tooltip cursor */
    span[title] {
        cursor: help;
    }

    /* Score display styling */
    .score-display {
        text-align: center;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

st.title("⚽ Football Buzz Dashboard")
st.caption("Welche Spiele sind diese Woche am spannendsten?")

# League selector at the top
selected_league = st.selectbox(
    "Liga auswählen",
    options=list(LEAGUES.keys()),
    index=0,  # Default to first league (Bundesliga)
    key="league_selector"
)

# Get league config
league_config = LEAGUES[selected_league]
competition = league_config["code"]
num_teams = league_config["teams"]

# Fetch standings
with st.spinner(f"Lade {selected_league} Tabelle..."):
    try:
        teams = fetch_standings(competition, num_teams)
        current_matchday = fetch_current_matchday(competition)
    except Exception as e:
        st.error(f"Fehler beim Laden der Daten: {e}")
        st.stop()

# Form loading toggle in sidebar
with st.sidebar:
    load_form = st.checkbox("Form laden (Liga und CL)", value=False,
                            help="Dauert ~2 Min beim ersten Mal (Rate Limit). Danach gecached.")
    if st.button("🔄 Cache leeren", help="Erzwingt Neuladen aller Daten"):
        st.cache_data.clear()
        st.rerun()

# Compute form for all teams (optional)
form_timestamp = None
if load_form:
    form_status = st.empty()
    form_status.info("⏳ Form-Daten werden geladen (~2 Min beim ersten Mal, danach gecached)")
    progress = st.progress(0, "Lade Form...")
    form_timestamp = compute_team_form(teams, progress)
    progress.empty()
    form_status.empty()

# Filters
col1, col2, col3 = st.columns([1, 2, 1])
with col1:
    selected_matchday = st.number_input(
        "Spieltag",
        min_value=1,
        max_value=34,
        value=current_matchday,
        step=1
    )
with col2:
    label_filter = st.multiselect(
        "Filter nach Typ",
        options=["🏆 Title Race", "🔥 Relegation Battle", "🎯 Head-to-Head", "⚡ Upset Potential", "⚔️ TITAN SLAYED"],
        default=[],
        placeholder="Alle Spiele anzeigen"
    )
with col3:
    only_upcoming = st.checkbox("Nur kommende Spiele", value=False)

# Fetch matches for selected matchday
with st.spinner(f"Lade Spieltag {selected_matchday}..."):
    try:
        matches = fetch_matches(competition, selected_matchday, teams)
    except Exception as e:
        st.error(f"Fehler beim Laden der Spiele: {e}")
        st.stop()

# Apply filters
if label_filter:
    matches = [m for m in matches if any(lbl in m.labels for lbl in label_filter)]

if only_upcoming:
    matches = [m for m in matches if not m.is_finished]

st.divider()

# Display matches sorted by buzz
if not matches:
    st.info("Keine Spiele gefunden für diesen Filter.")
else:
    st.subheader(f"Spieltag {selected_matchday} — sortiert nach Buzz-Score")

    for i, match in enumerate(matches):
        # Status indicator prefix
        if match.titan_slayed:
            status_indicator = "🏆"  # Trophy for titan slayed
        elif match.is_finished:
            status_indicator = "✅"  # Checkmark for finished
        else:
            status_indicator = "⏳"  # Hourglass for upcoming

        with st.container():
            if match.is_finished:
                # Finished match: score in the middle, buzz on the right
                team_cols = st.columns([0.3, 0.8, 2.2, 1.2, 0.8, 2.2, 1.5, 2])

                # Status indicator
                team_cols[0].markdown(f"<span style='font-size:1.2rem;'>{status_indicator}</span>",
                                      unsafe_allow_html=True)

                # Home icon
                if match.home.crest_url:
                    team_cols[1].image(match.home.crest_url, width=50)

                # Home team
                team_cols[2].markdown(f"**{match.home.short_name}**")
                team_cols[2].markdown(f"<small>#{match.home.position} • {match.home.form_display_html()}</small>",
                                      unsafe_allow_html=True)

                # Score in the middle
                team_cols[3].markdown(
                    f"<h2 style='text-align:center; margin:0;'>{match.home_score} : {match.away_score}</h2>",
                    unsafe_allow_html=True)

                # Away icon
                if match.away.crest_url:
                    team_cols[4].image(match.away.crest_url, width=50)

                # Away team
                team_cols[5].markdown(f"**{match.away.short_name}**")
                team_cols[5].markdown(f"<small>#{match.away.position} • {match.away.form_display_html()}</small>",
                                      unsafe_allow_html=True)

                # Buzz score
                team_cols[6].markdown(
                    f"<p style='font-size:0.7rem; margin:0; color:gray;'>Buzz:</p><p style='font-size:1.4rem; font-weight:bold; margin:0;'>🔥 {match.buzz:.0f}</p>",
                    unsafe_allow_html=True)

                # Labels
                if match.labels:
                    team_cols[7].markdown(" ".join(match.labels))

            else:
                # Upcoming match: buzz score on the right
                team_cols = st.columns([0.3, 0.8, 2.4, 0.5, 0.8, 2.4, 1.5, 2])

                # Status indicator
                team_cols[0].markdown(f"<span style='font-size:1.2rem;'>{status_indicator}</span>",
                                      unsafe_allow_html=True)

                # Home icon
                if match.home.crest_url:
                    team_cols[1].image(match.home.crest_url, width=50)

                # Home team
                team_cols[2].markdown(f"**{match.home.short_name}**")
                team_cols[2].markdown(f"<small>#{match.home.position} • {match.home.form_display_html()}</small>",
                                      unsafe_allow_html=True)

                # vs
                team_cols[3].markdown("—")

                # Away icon
                if match.away.crest_url:
                    team_cols[4].image(match.away.crest_url, width=50)

                # Away team
                team_cols[5].markdown(f"**{match.away.short_name}**")
                team_cols[5].markdown(f"<small>#{match.away.position} • {match.away.form_display_html()}</small>",
                                      unsafe_allow_html=True)

                # Buzz score
                team_cols[6].markdown(
                    f"<p style='font-size:0.7rem; margin:0; color:gray;'>Buzz:</p><p style='font-size:1.4rem; font-weight:bold; margin:0;'>🔥 {match.buzz:.0f}</p>",
                    unsafe_allow_html=True)

                # Labels + Kickoff
                team_cols[7].caption(f"📅 {format_kickoff(match.kickoff)}")
                if match.labels:
                    team_cols[7].markdown(" ".join(match.labels))

        st.divider()

    # Calculate dynamic relegation position for current league
    relegation_min_pos = RELEGATION_MIN_POS + (num_teams - 18)

    # Legend (collapsible for mobile)
    with st.expander("📋 Label-Legende & Buzz-Statistik", expanded=False):
        legend_cols = st.columns(2)

        with legend_cols[0]:
            st.markdown("**Primär**")
            st.markdown(f"🏆 **Title Race** — Beide ≤ Platz {TITLE_RACE_MAX_POS}, ≤ {CLOSE_MATCH_DISTANCE} Plätze")
            st.markdown(f"🔥 **Relegation** — Beide ≥ Platz {relegation_min_pos}, ≤ {CLOSE_MATCH_DISTANCE} Plätze")
            st.markdown("**Sekundär**")
            st.markdown(f"🎯 **Head-to-Head** — ≤ {CLOSE_MATCH_POINTS} Punkte Differenz")
            st.markdown(f"⚡ **Upset Potential** — ≥ {UPSET_DISTANCE} Plätze Abstand")
            st.markdown(f"⚔️ **TITAN SLAYED** — Underdog gewinnt bei ≥ {UPSET_DISTANCE} Plätze Abstand")

        with legend_cols[1]:
            st.markdown(f"**Buzz-Score ({selected_league})**")
            st.markdown(f"""
            | Label | Erwarteter Buzz |
            |-------|-----------------|
            | 🏆 Title Race | 70 - 100 |
            | 🔥 Relegation | 5 - 30 |
            | ⚡ Upset | 25 - 50 |
            | Maximum | 100 (1. vs 1.) |

            *Normalisiert auf 0-100 Skala*
            """)
            st.markdown("**Status-Indikatoren**")
            st.markdown("🏆 = TITAN SLAYED")
            st.markdown("✅ = Spiel beendet")
            st.markdown("⏳ = Kommend")

    # Form timestamp hint
    if form_timestamp:
        st.caption(f"ℹ️ Team-Form zuletzt abgerufen: {form_timestamp} (Cache: ~100 Min)")

# Sidebar: Current Table
with st.sidebar:
    st.header(f"📊 {selected_league}")

    for team in sorted(teams.values(), key=lambda t: t.position):
        col_crest, col_info = st.columns([1, 4])
        if team.crest_url:
            col_crest.image(team.crest_url, width=25)
        col_info.markdown(f"**{team.position}.** {team.short_name}")
        col_info.markdown(f"<small>{team.points} Pkt • {team.form_display_html()}</small>",
                          unsafe_allow_html=True)

    st.divider()
    st.header("ℹ️ Scoring-Logik")
    st.markdown(f"""
    **Buzz-Score** (0-100 normalisiert)

    `Buzz = (Base × Closeness) / Max × 100`

    - **Base** = `({num_teams} - Pos_A) + ({num_teams} - Pos_B)`
    - **Closeness** = `1 + ({num_teams - 1} - Abstand) / {num_teams - 1}`
    - **Max** = {4 * (num_teams - 1)} (1. vs 1.)

    **Primär:**
    - 🏆 **Title Race**: Beide ≤ Platz {TITLE_RACE_MAX_POS}, ≤ {CLOSE_MATCH_DISTANCE} Plätze
    - 🔥 **Relegation**: Beide ≥ Platz {RELEGATION_MIN_POS + (num_teams - 18)}, ≤ {CLOSE_MATCH_DISTANCE} Plätze

    **Sekundär:**
    - 🎯 **Head-to-Head**: ≤ {CLOSE_MATCH_POINTS} Pkt Differenz
    - ⚡ **Upset**: ≥ {UPSET_DISTANCE} Plätze Abstand
    - ⚔️ **TITAN SLAYED**: Underdog gewinnt

    **Form:** 🟢 Sieg 🟡 Unentschieden 🔴 Niederlage

    *💡 Hover über Form-Symbole für Details*
    """)