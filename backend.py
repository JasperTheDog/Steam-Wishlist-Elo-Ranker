import asyncio
import json
import random
import shutil
from pathlib import Path
from typing import Dict, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
WISHLIST_FILE = DATA_DIR / "wishlist.json"
HISTORY_FILE = DATA_DIR / "history.json"
STATS_FILE = DATA_DIR / "stats.json"
TEMP_DIR = DATA_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)
VERSION_FILE = DATA_DIR / "version.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

# ensure dirs
DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

app = FastAPI()
# -----------------------
# Models
# -----------------------
class Settings(BaseModel):
    winner_stays_on: bool = False
    prefer_close_rating: bool = False
    prefer_far_rating: bool = False
    prefer_lower_played: bool = False

class Game(BaseModel):
    appid: str
    title: str
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    rating: float = 1500.0
    wins: int = 0
    losses: int = 0
    played: int = 0

class PairResponse(BaseModel):
    a: Game
    b: Game

class VotePayload(BaseModel):
    winner_appid: str
    loser_appid: str

# -----------------------
# Utility: load/save
# -----------------------
def load_wishlist() -> Dict[str, Dict]:
    if WISHLIST_FILE.exists():
        return json.loads(WISHLIST_FILE.read_text())
    return {}

def save_wishlist(d: Dict[str, Dict]):
    WISHLIST_FILE.write_text(json.dumps(d, indent=2))

def append_history(entry: Dict):
    hist = []
    if HISTORY_FILE.exists():
        hist = json.loads(HISTORY_FILE.read_text())
    hist.append(entry)
    HISTORY_FILE.write_text(json.dumps(hist, indent=2))

def load_stats() -> Dict[str, int]:
    if STATS_FILE.exists():
        return json.loads(STATS_FILE.read_text())
    return {"total_played": 0}

def save_stats(stats: Dict[str, int]):
    STATS_FILE.write_text(json.dumps(stats, indent=2))

def load_settings() -> Dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {
        "winner_stays_on": False,
        "prefer_close_rating": False,
        "prefer_far_rating": False,
        "prefer_lower_played": False,
    }

def save_settings(d: Dict):
    SETTINGS_FILE.write_text(json.dumps(d, indent=2))

@app.get("/stats")
def stats():
    s = load_stats()
    return s

def load_version() -> int:
    if VERSION_FILE.exists():
        return json.loads(VERSION_FILE.read_text()).get("version", 0)
    return 0

def increment_version() -> int:
    v = load_version() + 1
    VERSION_FILE.write_text(json.dumps({"version": v}, indent=2))
    return v


# -----------------------
# Elo functions
# -----------------------
def expected_score(r_a: float, r_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))

def update_elo(r_winner: float, r_loser: float, k: float = 24.0):
    e_win = expected_score(r_winner, r_loser)
    e_lose = expected_score(r_loser, r_winner)
    r_winner_new = r_winner + k * (1 - e_win)
    r_loser_new = r_loser + k * (0 - e_lose)
    return r_winner_new, r_loser_new

# -----------------------
# Wishlist import
# -----------------------
@app.post("/import/json")
async def import_json(payload: Request):
    """
    Upload a JSON body of games: list of {appid, title, image_url?}
    (Use this if you export from steam or create manually).
    """
    body = await payload.json()
    if not isinstance(body, list):
        raise HTTPException(400, "Expected a list of games")

    data = load_wishlist()
    for entry in body:
        appid = str(entry.get("appid") or entry.get("id") or entry.get("appid"))
        title = entry.get("title") or entry.get("name") or "Untitled"
        image_url = entry.get("image_url")
        if not appid:
            continue
        if appid in data:
            # update if needed
            data[appid].setdefault("title", title)
            if image_url:
                data[appid]["image_url"] = image_url
        else:
            data[appid] = {
                "appid": appid,
                "title": title,
                "image_url": image_url,
                "image_path": None,
                "rating": 1500.0,
                "wins": 0,
                "losses": 0,
                "played": 0
            }
    save_wishlist(data)
    return {"imported": len(body), "total": len(data)}

# -----------------------
# Game 
# -----------------------
@app.get("/game_info/{appid}")
def game_info(appid: str):
    data = load_wishlist()
    stats = {}
    if (DATA_DIR / "stats.json").exists():
        stats = json.loads((DATA_DIR / "stats.json").read_text())

    g = data.get(appid)
    if not g:
        raise HTTPException(404, "Game not found")

    wins = g.get("wins", 0)
    losses = g.get("losses", 0)
    played = g.get("played", 0)
    winrate = round(100 * wins / played, 1) if played else 0

    return {
        "appid": g["appid"],
        "title": g["title"],
        "image_url": g.get("image_url"),
        "image_path": g.get("image_path"),
        "rating": g.get("rating", 1500),
        "wins": wins,
        "losses": losses,
        "played": played,
        "winrate": winrate
    }


@app.get("/game_history/{appid}")
def game_history(appid: str):
    history = []
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text())
    filtered = [h for h in history if h["winner"] == appid or h["loser"] == appid]
    return {"count": len(filtered), "history": filtered}


@app.post("/delete_game/{appid}")
def delete_game(appid: str):
    data = load_wishlist()
    history = []
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text())

    if appid not in data:
        raise HTTPException(404, "Game not found")

    # remove game + history
    data.pop(appid, None)
    history = [h for h in history if h["winner"] != appid and h["loser"] != appid]

    save_wishlist(data)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))

    return {"status": "ok", "message": f"Game {appid} deleted"}

# -----------------------
# Settings endpoints
# -----------------------
@app.get("/settings")
def get_settings():
    return JSONResponse(content=load_settings())

@app.post("/settings")
async def update_settings(payload: Settings):
    save_settings(payload.dict())
    return {"status": "ok", "settings": payload.dict()}

# -----------------------
# Image caching
# -----------------------
async def cache_image_for_game(game: Dict) -> Optional[str]:
    """
    Download image if image_url given. Return local path or None.
    """
    url = game.get("image_url")
    if not url:
        return None
    appid = game["appid"]
    ext = url.split("?")[0].split(".")[-1]
    local = CACHE_DIR / f"{appid}.{ext}"
    if local.exists():
        game["image_path"] = str(local)
        return str(local)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                local.write_bytes(resp.content)
                game["image_path"] = str(local)
                save_wishlist(load_wishlist())  # ensure path persisted
                return str(local)
    except Exception as e:
        print("image cache error:", e)
    return None

# -----------------------
# Pairing endpoint
# -----------------------
@app.get("/pair", response_model=PairResponse)
async def pair(
    prefer_close_rating: bool = False,
    prefer_far_rating: bool = False,
    prefer_lower_played: bool = False,
    challenger: Optional[str] = None,
):
    data = load_wishlist()
    games = list(data.values())
    if len(games) < 2:
        raise HTTPException(400, "Need at least 2 games in wishlist")

    def pick_from_top(sorted_games, exclude_appid=None):
        top_n = max(2, int(len(sorted_games) * 0.1))
        candidates = [g for g in sorted_games if g["appid"] != exclude_appid] if exclude_appid else sorted_games
        candidates = candidates[:top_n]
        return random.choice(candidates) if candidates else None

    def multi_sort(games, g1=None):
        # Compose sort keys based on preferences
        def sort_key(g):
            keys = []
            # Sort by lower player first if preferred
            if prefer_lower_played:
                keys.append(g.get("played", 0))
            # Sort by rating difference if g1 is given
            if g1:
                if prefer_close_rating:
                    keys.append(abs(g["rating"] - g1["rating"]))
                elif prefer_far_rating:
                    keys.append(-abs(g["rating"] - g1["rating"]))
            return tuple(keys)
        return sorted(games, key=sort_key)

    # If challenger mode is active, always use challenger as g1
    if challenger and challenger in data:
        g1 = data[challenger]
        possible_opponents = [g for g in games if g["appid"] != challenger]
        if not possible_opponents:
            raise HTTPException(400, "Not enough games to find an opponent.")

        sorted_opponents = multi_sort(possible_opponents, g1)
        g2 = pick_from_top(sorted_opponents)
    else:
        g1 = pick_from_top(multi_sort(games))
        sorted_games = multi_sort([g for g in games if g["appid"] != g1["appid"]], g1)
        g2 = pick_from_top(sorted_games)

    if not g1 or not g2:
        g1, g2 = random.sample(games, 2)

    asyncio.create_task(cache_image_for_game(g1))
    asyncio.create_task(cache_image_for_game(g2))

    return {"a": g1, "b": g2}

# -----------------------
# Vote endpoint
# -----------------------
@app.post("/vote")
def vote(payload: VotePayload, k: float = 24.0):
    data = load_wishlist()
    winner = data.get(payload.winner_appid)
    loser = data.get(payload.loser_appid)
    if not winner or not loser:
        raise HTTPException(400, "Invalid appid(s)")

    r_w = float(winner["rating"])
    r_l = float(loser["rating"])
    r_w_new, r_l_new = update_elo(r_w, r_l, k=k)

    winner["rating"] = r_w_new
    loser["rating"] = r_l_new
    winner["wins"] = int(winner.get("wins", 0)) + 1
    loser["losses"] = int(loser.get("losses", 0)) + 1
    winner["played"] = int(winner.get("played", 0)) + 1
    loser["played"] = int(loser.get("played", 0)) + 1

    save_wishlist(data)
    append_history({
        "winner": winner["appid"],
        "winner_title": winner["title"],
        "winner_image": winner.get("image_path") or winner.get("image_url"),
        "loser": loser["appid"],
        "loser_title": loser["title"],
        "loser_image": loser.get("image_path") or loser.get("image_url"),
        "r_w_before": r_w,
        "r_l_before": r_l,
        "r_w_after": r_w_new,
        "r_l_after": r_l_new,
        "k": k
    })
    # Update total games played
    stats = load_stats()
    stats["total_played"] = stats.get("total_played", 0) + 1
    save_stats(stats)
    return {"winner": winner["appid"], "new_rating_winner": r_w_new, "new_rating_loser": r_l_new}

# -----------------------
# Pass endpoint
# -----------------------
@app.post("/pass")
def pass_vote(a_appid: str, b_appid: str, k: float = 24.0):
    data = load_wishlist()
    a = data.get(a_appid)
    b = data.get(b_appid)
    if not a or not b:
        raise HTTPException(400, "Invalid appid(s)")

    if a["rating"] <= b["rating"]:
        winner, loser = a, b
    else:
        winner, loser = b, a

    r_w = float(winner["rating"])
    r_l = float(loser["rating"])
    r_w_new, r_l_new = update_elo(r_w, r_l, k=k)

    winner["rating"] = r_w_new
    loser["rating"] = r_l_new
    winner["wins"] += 1
    loser["losses"] += 1
    winner["played"] += 1
    loser["played"] += 1

    save_wishlist(data)
    append_history({
        "winner": winner["appid"],
        "winner_title": winner["title"],
        "winner_image": winner.get("image_path") or winner.get("image_url"),
        "loser": loser["appid"],
        "loser_title": loser["title"],
        "loser_image": loser.get("image_path") or loser.get("image_url"),
        "pass": True,
        "r_w_before": r_w,
        "r_l_before": r_l,
        "r_w_after": r_w_new,
        "r_l_after": r_l_new,
        "k": k
    })
    stats = load_stats()
    stats["total_played"] = stats.get("total_played", 0) + 1
    save_stats(stats)
    return {"winner": winner["appid"], "via": "pass"}

# -----------------------
# Listing & export
# -----------------------
@app.get("/ranked")
def ranked(limit: int = 200):
    data = load_wishlist()
    games = sorted(data.values(), key=lambda g: g["rating"], reverse=True)
    return {"count": len(games), "games": games[:limit]}

@app.get("/game_image/{appid}")
def game_image(appid: str):
    data = load_wishlist()
    g = data.get(appid)
    if not g:
        raise HTTPException(404, "Not found")
    if g.get("image_path") and Path(g["image_path"]).exists():
        return FileResponse(g["image_path"])
    raise HTTPException(404, "No cached image")

@app.get("/download/export.json")
def export_json():
    return JSONResponse(content=load_wishlist())

# -----------------------
# Small search endpoint
# -----------------------
@app.get("/search")
def search(q: str):
    data = load_wishlist()
    results = [g for g in data.values() if q.lower() in g["title"].lower()]
    return {"count": len(results), "results": results[:50]}

# -----------------------
# Leaderboard endpoint
# -----------------------
@app.get("/leaderboard")
def leaderboard(q: str = "", limit: int = 200):
    data = load_wishlist()
    games = sorted(data.values(), key=lambda g: g["rating"], reverse=True)
    if q:
        games = [g for g in games if q.lower() in g["title"].lower()]
    # Add win rate
    for g in games:
        played = g.get("played", 0)
        wins = g.get("wins", 0)
        g["winrate_percent"] = round((wins / played) * 100, 1) if played else 0.0
    return {"count": len(games), "games": games[:limit]}

# -----------------------
# History endpoint
# -----------------------
@app.get("/history")
def get_history():
    """
    Return the battle history (array of past votes), most recent first,
    with winner/loser titles and images filled in from current wishlist.
    """
    if HISTORY_FILE.exists():
        hist = json.loads(HISTORY_FILE.read_text())
    else:
        hist = []
    games = load_wishlist()
    out = []
    for h in reversed(hist):
        winner = games.get(h["winner"], {})
        loser = games.get(h["loser"], {})
        out.append({
            **h,
            "winner_title": winner.get("title", h.get("winner", "")),
            "winner_image": winner.get("image_path") or winner.get("image_url", ""),
            "loser_title": loser.get("title", h.get("loser", "")),
            "loser_image": loser.get("image_path") or loser.get("image_url", ""),
        })
    return {"count": len(out), "history": out}

# -----------------------
# Danger Zone endpoints
# -----------------------
@app.post("/reset_ratings")
def reset_ratings():
    """
    Backup wishlist.json, history.json, stats.json and reset ratings to default.
    """
    # Load version and increment
    version = increment_version()
    backup_folder = TEMP_DIR / f"{version}"
    backup_folder.mkdir(exist_ok=True)

    # Files to backup
    files_to_backup = [WISHLIST_FILE, HISTORY_FILE, STATS_FILE]
    for f in files_to_backup:
        if f.exists():
            dest = backup_folder / f.name
            dest.write_bytes(f.read_bytes())

    # Reset wishlist.json ratings, wins, losses, played
    wishlist = load_wishlist()
    for g in wishlist.values():
        g["rating"] = 1500.0
        g["wins"] = 0
        g["losses"] = 0
        g["played"] = 0
    save_wishlist(wishlist)

    # Reset history.json to empty list
    HISTORY_FILE.write_text(json.dumps([], indent=2))

    # Reset stats.json
    save_stats({"total_played": 0})

    return {"message": "All ratings, history, and stats reset. Backup created at temp/" + str(version)}

@app.get("/list_backups")
def list_backups():
    backups = sorted([d.name for d in TEMP_DIR.iterdir() if d.is_dir()], key=int)
    return {"backups": backups}

@app.post("/restore_version")
def restore_version(payload: dict):
    version = str(payload.get("version"))
    folder = TEMP_DIR / version
    if not folder.exists():
        raise HTTPException(404, "Backup version not found")

    files_to_restore = [WISHLIST_FILE, HISTORY_FILE, STATS_FILE]
    for f in files_to_restore:
        backup_file = folder / f.name
        if backup_file.exists():
            f.write_bytes(backup_file.read_bytes())

    return {"message": f"Restored version {version} successfully."}

@app.post("/save_snapshot")
def save_snapshot():
    version = increment_version()
    backup_folder = TEMP_DIR / f"{version}"
    backup_folder.mkdir(exist_ok=True)

    files_to_backup = [WISHLIST_FILE, HISTORY_FILE, STATS_FILE]
    for f in files_to_backup:
        if f.exists():
            dest = backup_folder / f.name
            dest.write_bytes(f.read_bytes())

    return {"message": f"Snapshot saved as version {version}."}

# -----------------------
# Frontend serving
# -----------------------
@app.get("/game/{appid}", response_class=HTMLResponse)
def game_page(appid: str):
    game_html = (APP_DIR / "static" / "game.html").read_text()
    return HTMLResponse(content=game_html)

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)
