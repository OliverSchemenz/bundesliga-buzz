import streamlit as st
import requests
import time
from dataclasses import dataclass, field

# --- Configuration ---
# NOTE: Season is the year the season STARTS (e.g., 2024 for 2024/25 season)
SEASON = 2025
COMPETITION = "BL1"  # Bundesliga

# football-data.org API
API_BASE = "https://api.football-data.org/v4"
API_KEY = "f1cc4ca1173d4528b60411d86ad12b2c"

# Label thresholds
TITLE_RACE_MAX_POS = 8
RELEGATION_MIN_POS = 13
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
    crest_url: str = ""
    form: list[FormMatch] = field(default_factory=list)

    @property
    def buzz_score(self) -> int:
        """Higher table position = higher buzz score (1st place = 18 pts)"""
        return 19 - self.position

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
        return 1 + (17 - self.position_distance) / 17

    @property
    def buzz(self) -> float:
        return self.base_score * self.closeness_bonus

    @property
    def labels(self) -> list[str]:
        labels = []
        highest_pos = min(self.home.position, self.away.position)
        lowest_pos = max(self.home.position, self.away.position)

        # PRIMARY LABELS (based on table position)
        # Title Race: both teams near the top and close in standings
        if lowest_pos <= TITLE_RACE_MAX_POS and self.position_distance <= CLOSE_MATCH_DISTANCE:
            labels.append("🏆 Title Race")

        # Relegation Battle: both teams near the bottom and close in standings
        if highest_pos >= RELEGATION_MIN_POS and self.position_distance <= CLOSE_MATCH_DISTANCE:
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
            # Rate limited - wait and retry
            retry_after = int(response.headers.get("X-RequestCounter-Reset", 60))
            st.warning(f"Rate limit erreicht, warte {retry_after}s...")
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


@st.cache_data(ttl=300)  # Cache for 5 minutes
def fetch_standings_cached() -> list[dict]:
    """Fetch current Bundesliga standings (cached)."""
    data = api_request(f"/competitions/{COMPETITION}/standings", {"season": SEASON})
    return data["standings"][0]["table"]


def fetch_standings() -> dict[int, Team]:
    """Build Team dict from cached standings."""
    standings = fetch_standings_cached()

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
            crest_url=team_data.get("crest", "")
        )

    return teams


@st.cache_data(ttl=300)
def fetch_current_matchday() -> int:
    """Get the current matchday number (cached)."""
    data = api_request(f"/competitions/{COMPETITION}", {"season": SEASON})
    return data["currentSeason"]["currentMatchday"]


@st.cache_data(ttl=300)
def fetch_matches_cached(matchday: int) -> list[dict]:
    """Fetch matches for a specific matchday (cached)."""
    data = api_request(
        f"/competitions/{COMPETITION}/matches",
        {"season": SEASON, "matchday": matchday}
    )
    return data.get("matches", [])


def fetch_matches(matchday: int, teams: dict[int, Team]) -> list[Match]:
    """Fetch matches for a specific matchday."""
    matches_data = fetch_matches_cached(matchday)

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


@st.cache_data(ttl=600, show_spinner=False)  # Cache form for 10 minutes
def fetch_team_form_cached(team_id: int) -> list[dict]:
    """Fetch last 5 match results for a team (all competitions)."""
    # Rate limit delay - only executes when not cached
    time.sleep(REQUEST_DELAY)

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

        return form

    except Exception:
        return []


def compute_team_form(teams: dict[int, Team], progress_bar=None) -> None:
    """Fetch last 5 match results for each team (all competitions)."""
    team_list = list(teams.items())

    for i, (team_id, team) in enumerate(team_list):
        form_data = fetch_team_form_cached(team_id)
        team.form = [FormMatch(**fm) for fm in form_data]

        if progress_bar:
            progress_bar.progress((i + 1) / len(team_list), f"Lade Form: {team.short_name}")


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
st.set_page_config(page_title="Bundesliga Buzz", page_icon="⚽", layout="wide")

# Mobile-responsive CSS
st.markdown("""
<style>
    /* Compact metrics */
    [data-testid="stMetricValue"] {
        font-size: 1.2rem !important;
    }
    [data-testid="stMetricLabel"] {
        display: none !important;
    }

    /* Mobile adjustments */
    @media (max-width: 640px) {
        [data-testid="stMetricValue"] {
            font-size: 1rem !important;
        }
        img {
            max-width: 28px !important;
        }
        .stMarkdown small {
            font-size: 0.7rem !important;
        }
    }

    /* Tooltip cursor */
    span[title] {
        cursor: help;
    }
</style>
""", unsafe_allow_html=True)

st.title("⚽ Bundesliga Buzz Dashboard")
st.caption("Welche Spiele sind diese Woche am spannendsten?")

# Fetch standings
with st.spinner("Lade Tabelle..."):
    try:
        teams = fetch_standings()
        current_matchday = fetch_current_matchday()
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
if load_form:
    form_status = st.empty()
    form_status.info("⏳ Form-Daten werden geladen (~2 Min beim ersten Mal, danach gecached)")
    progress = st.progress(0, "Lade Form...")
    compute_team_form(teams, progress)
    progress.empty()
    form_status.empty()

# Filters
col1, col2 = st.columns([1, 2])
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
        options=["🏆 Title Race", "🔥 Relegation Battle", "🎯 Head-to-Head", "⚡ Upset Potential"],
        default=[],
        placeholder="Alle Spiele anzeigen"
    )

# Fetch matches for selected matchday
with st.spinner(f"Lade Spieltag {selected_matchday}..."):
    try:
        matches = fetch_matches(selected_matchday, teams)
    except Exception as e:
        st.error(f"Fehler beim Laden der Spiele: {e}")
        st.stop()

# Apply label filter
if label_filter:
    matches = [m for m in matches if any(lbl in m.labels for lbl in label_filter)]

st.divider()

# Display matches sorted by buzz
if not matches:
    st.info("Keine Spiele gefunden für diesen Filter.")
else:
    st.subheader(f"Spieltag {selected_matchday} — sortiert nach Buzz-Score")

    for i, match in enumerate(matches):
        with st.container():
            # Compact layout: 2 main columns (match info | buzz/labels)
            main_cols = st.columns([4, 1.5])

            with main_cols[0]:
                # Match info in sub-columns
                team_cols = st.columns([0.6, 3, 0.5, 0.6, 3])

                # Home icon
                if match.home.crest_url:
                    team_cols[0].image(match.home.crest_url, width=35)

                # Home team
                team_cols[1].markdown(f"**{match.home.short_name}**")
                team_cols[1].markdown(f"<small>#{match.home.position} • {match.home.form_display_html()}</small>",
                                      unsafe_allow_html=True)

                # vs
                team_cols[2].markdown("—")

                # Away icon
                if match.away.crest_url:
                    team_cols[3].image(match.away.crest_url, width=35)

                # Away team
                team_cols[4].markdown(f"**{match.away.short_name}**")
                team_cols[4].markdown(f"<small>#{match.away.position} • {match.away.form_display_html()}</small>",
                                      unsafe_allow_html=True)

            with main_cols[1]:
                # Buzz score or final result
                if match.is_finished:
                    st.metric("", f"{match.home_score}:{match.away_score}")
                else:
                    st.metric("", f"🔥 {match.buzz:.0f}")
                    st.caption(f"📅 {format_kickoff(match.kickoff)}")

                # Labels (show for both finished and upcoming)
                if match.labels:
                    st.caption(" ".join(match.labels))

            st.divider()

    # Legend (collapsible for mobile)
    with st.expander("📋 Label-Legende", expanded=False):
        legend_cols = st.columns(2)

        with legend_cols[0]:
            st.markdown("**Primär**")
            st.markdown(f"🏆 **Title Race** — Beide ≤ Platz {TITLE_RACE_MAX_POS}, ≤ {CLOSE_MATCH_DISTANCE} Plätze")
            st.markdown(f"🔥 **Relegation** — Beide ≥ Platz {RELEGATION_MIN_POS}, ≤ {CLOSE_MATCH_DISTANCE} Plätze")

        with legend_cols[1]:
            st.markdown("**Sekundär**")
            st.markdown(f"🎯 **Head-to-Head** — ≤ {CLOSE_MATCH_POINTS} Punkte Differenz")
            st.markdown(f"⚡ **Upset Potential** — ≥ {UPSET_DISTANCE} Plätze Abstand")

# Sidebar: Current Table
with st.sidebar:
    st.header("📊 Aktuelle Tabelle")

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
    **Buzz-Score** = Base × Closeness

    - **Base** = `(19 - Pos_A) + (19 - Pos_B)`
    - **Closeness** = `1 + (17 - Abstand) / 17`

    **Primär:**
    - 🏆 **Title Race**: Beide ≤ Platz {TITLE_RACE_MAX_POS}, ≤ {CLOSE_MATCH_DISTANCE} Plätze
    - 🔥 **Relegation**: Beide ≥ Platz {RELEGATION_MIN_POS}, ≤ {CLOSE_MATCH_DISTANCE} Plätze

    **Sekundär:**
    - 🎯 **Head-to-Head**: ≤ {CLOSE_MATCH_POINTS} Pkt Differenz
    - ⚡ **Upset**: ≥ {UPSET_DISTANCE} Plätze Abstand

    **Form:** 🟢 Sieg 🟡 Unentschieden 🔴 Niederlage

    *💡 Hover über Form-Symbole für Details*
    """)