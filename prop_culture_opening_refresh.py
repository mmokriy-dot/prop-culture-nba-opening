"""Refresh Prop Culture opening-event snapshots from the public hoopR parquet feed.
Requires: pandas, pyarrow, requests. Run via GitHub Actions or locally.
"""
from pathlib import Path
import re
import pandas as pd

SOURCE = 'https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/pbp/parquet/play_by_play_2027.parquet'
OUT = Path('data')
OUT.mkdir(exist_ok=True)

raw = pd.read_parquet(SOURCE)
raw = raw[raw['period_number'].astype(str).str.replace('.0','',regex=False).eq('1')].copy()
raw['sequence_number'] = pd.to_numeric(raw['sequence_number'], errors='coerce')
raw = raw.sort_values(['game_id', 'sequence_number'])

records = []
for game_id, group in raw.groupby('game_id', sort=False):
    first = group.iloc[0]
    away, home = str(first.get('away_team_abbrev','')), str(first.get('home_team_abbrev',''))
    away_id, home_id = str(first.get('away_team_id','')), str(first.get('home_team_id',''))
    jump = group[group['type_text'].astype(str).str.contains('Jumpball', case=False, na=False)]
    jump = jump.iloc[0] if not jump.empty else None
    jumptext = str(jump['text']) if jump is not None else ''
    match = re.search(r'^(.*?)\s+vs\.\s+(.*?)\s+\((.*?)\s+gains possession\)',jumptext)
    jumper1, jumper2, poss = match.groups() if match else ('','','')
    # Possession is credited only when an explicit team identifier can be resolved.
    # The initial possession player may differ from either jumper.
    poss_team = ''
    if jump is not None and match:
        # Team ID on a jump event can represent event attribution, not necessarily winner.
        # Prefer explicit possession athlete ID match against an observed roster mapping.
        athlete_rows = group[['team_id','athlete_name_1','athlete_name_2','athlete_name_3']].copy()
        found=set()
        for _, r in athlete_rows.iterrows():
            tid=str(r['team_id'])
            if tid not in (away_id,home_id):continue
            for col in ('athlete_name_1','athlete_name_2','athlete_name_3'):
                if str(r[col]).strip()==poss:found.add(away if tid==away_id else home)
        if len(found)==1:poss_team=next(iter(found))
    shots = group[group['shooting_play'].astype(str).str.lower().isin(['true','1'])].copy()
    # shooting_play includes free throws in some feeds; use field-goal descriptions only.
    shots = shots[~shots['type_text'].astype(str).str.contains('free throw',case=False,na=False)]
    def get_shot(team=None):
        ss=shots if team is None else shots[shots['team_id'].astype(str)==str(team)]
        if ss.empty:return ('','','')
        s=ss.iloc[0]
        return (str(s.get('athlete_name_1','') or ''), str(s.get('text','') or ''), away if str(s['team_id'])==away_id else home if str(s['team_id'])==home_id else '')
    fs, desc, ft = get_shot()
    away_fs, away_desc, _ = get_shot(away_id)
    home_fs, home_desc, _ = get_shot(home_id)
    records.append(dict(game_id=game_id,date=str(first.get('game_date',''))[:10],away=away,home=home,
        jump_player_1=jumper1,jump_player_2=jumper2,possession_player=poss,
        opening_tip_team=poss_team,opening_tip_verified=bool(poss_team),
        game_first_shooter=fs,game_first_shot_team=ft,game_first_shot_description=desc,
        away_first_shooter=away_fs,away_first_shot_description=away_desc,
        home_first_shooter=home_fs,home_first_shot_description=home_desc))

games=pd.DataFrame(records).sort_values(['date','game_id'])
# Fail safely if the upstream source schema or mapping is unusable.
if games.empty or games['game_first_shooter'].eq('').all():
    raise RuntimeError('No usable opening-shot data; refusing to publish an empty snapshot')
games.to_csv(OUT/'opening_game_results.csv',index=False)

teams=[]
for team in sorted(set(games.away)|set(games.home)):
    g=games[(games.away==team)|(games.home==team)]
    verified=g[g.opening_tip_verified]
    tip_wins=int((verified.opening_tip_team==team).sum())
    first_shots=int((g.game_first_shot_team==team).sum())
    teams.append(dict(team=team,games=len(g),verified_tip_games=len(verified),tip_wins=tip_wins,
        tip_win_pct=round(tip_wins/len(verified)*100,1) if len(verified) else 0,
        first_shots=first_shots,first_shot_pct=round(first_shots/len(g)*100,1)))
pd.DataFrame(teams).to_csv(OUT/'opening_team_summary.csv',index=False)

players=[]
for (team,player),g in games[games.game_first_shooter.ne('')].groupby(['game_first_shot_team','game_first_shooter']):
    denom=sum((games.away==team)|(games.home==team))
    players.append(dict(team=team,player=player,first_shots=len(g),team_games=denom,
                        first_shot_pct=round(len(g)/denom*100,1) if denom else 0))
pd.DataFrame(players).sort_values('first_shots',ascending=False).to_csv(OUT/'opening_player_summary.csv',index=False)
print(f'Published {len(games)} games, {int(games.opening_tip_verified.sum())} verified tips, {len(players)} first shooters')
