"""
NCAA Trading Bot Dashboard
Simple GUI showing positions, fair values, orderbook, and Kalshi odds.
Auto-refreshes every 10 seconds. Click column headers to sort.
"""

import asyncio
import re
import tkinter as tk
from tkinter import ttk
import threading
import time
import aiohttp

from model import compute_fair_values, TEAM_RATINGS, SYMBOL_TO_TEAM, TEAM_TO_SYMBOL, update_symbol_mapping

# Copy these from bot.py
GAME_ID = 160
TOKEN = "REDACTED_DRW_TOKEN"
BASE_URL = "https://games.drw.com"
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"

REFRESH_INTERVAL = 10  # seconds


def _game_api_url(base_url, game_id):
    return f"{base_url}/api/games/trading-simulator/{game_id}"


# Kalshi ticker abbreviation -> model team name
KALSHI_ABBREV_TO_TEAM = {
    "ARIZ": "Arizona", "ALA": "Alabama", "ARK": "Arkansas", "BYU": "BYU",
    "CLEM": "Clemson", "DUKE": "Duke", "FLA": "Florida", "GONZ": "Gonzaga",
    "HOU": "Houston", "ILL": "Illinois", "ISU": "Iowa State", "KU": "Kansas",
    "UK": "Kentucky", "LOU": "Louisville", "MICH": "Michigan", "MSU": "Michigan State",
    "MIZ": "Missouri", "NEB": "Nebraska", "UNC": "North Carolina", "NCST": "NC State",
    "OSU": "Ohio State", "PUR": "Purdue", "SJU": "St. John's", "SMU": "SMU",
    "TENN": "Tennessee", "TEX": "Texas", "TAMU": "Texas A&M", "TTU": "Texas Tech",
    "TCU": "TCU", "UCLA": "UCLA", "CONN": "Connecticut", "UCONN": "Connecticut",
    "UVA": "Virginia", "VAN": "Vanderbilt", "NOVA": "Villanova", "WIS": "Wisconsin",
    "VILL": "Villanova", "GU": "Gonzaga", "UGA": "Georgia", "MIA": "Miami FL",
    "IOWA": "Iowa", "VCU": "VCU", "UCF": "UCF", "USF": "South Florida",
    "SLU": "Saint Louis", "SMC": "Saint Mary's", "SCU": "Santa Clara",
    "USU": "Utah State", "MCNS": "McNeese", "HP": "High Point", "HOF": "Hofstra",
    "TROY": "Troy", "HAW": "Hawaii", "MIOH": "Miami OH", "PENN": "Penn",
    "KNST": "Kennesaw State", "NDSU": "North Dakota State", "CBU": "Cal Baptist",
    "WRST": "Wright State", "FUR": "Furman", "QUEE": "Queens", "IDHO": "Idaho",
    "TNST": "Tennessee State", "SIEN": "Siena", "LIU": "Long Island",
    "LEHI": "Lehigh", "PVAM": "Prairie View A&M", "HOW": "Howard",
    "UMBC": "UMBC", "AKR": "Akron", "NI": "Northern Iowa",
}


class DashboardData:
    """Holds all data fetched by the background thread."""
    def __init__(self):
        self.positions = {}
        self.cash = 0.0
        self.margin = 0.0
        self.fair_values = {}
        self.order_books = {}  # symbol -> {best_bid, best_ask}
        self.kalshi_prices = {}  # team -> {champ_bid, champ_ask}
        self.matched_symbols = {}  # symbol -> team
        self.last_update = 0.0
        self.error = ""


async def fetch_kalshi_prices(session: aiohttp.ClientSession, data: DashboardData):
    """Fetch Kalshi NCAA championship market prices."""
    try:
        # Fetch markets from the KXMARMADROUND series (championship round = 26CHAMP or similar)
        # Also try other round series to get advancement odds
        all_markets = []
        cursor = ""
        for _ in range(5):  # paginate up to 5 pages
            params = {"series_ticker": "KXMARMADROUND", "status": "open", "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            async with session.get(f"{KALSHI_API_BASE}/markets", params=params) as resp:
                if resp.status != 200:
                    break
                d = await resp.json()
                markets = d.get("markets", [])
                all_markets.extend(markets)
                cursor = d.get("cursor", "")
                if not cursor or len(markets) < 200:
                    break

        # Parse: extract team from ticker suffix (e.g., KXMARMADROUND-26CHAMP-DUKE -> DUKE)
        # Group by team, prefer championship round, fallback to deepest round available
        team_round_prices = {}  # team -> {round_key: {bid, ask}}
        round_priority = {"CHAMP": 0, "F4": 1, "E8": 2, "S16": 3, "T2": 4}

        for m in all_markets:
            ticker = m.get("ticker", "")
            # Extract round and team abbrev from ticker like KXMARMADROUND-26S16-DUKE
            parts = ticker.split("-")
            if len(parts) < 3:
                continue

            round_part = parts[1].replace("26", "")  # e.g., "S16", "E8", "F4", "CHAMP"
            abbrev = parts[2]

            team = KALSHI_ABBREV_TO_TEAM.get(abbrev)
            if not team:
                # Try matching via title
                title = (m.get("title", "") + " " + m.get("subtitle", "")).lower()
                for t in sorted(TEAM_RATINGS.keys(), key=len, reverse=True):
                    if re.search(r'\b' + re.escape(t.lower()) + r'\b', title):
                        team = t
                        break
            if not team:
                continue

            yes_bid = m.get("yes_bid_dollars")
            yes_ask = m.get("yes_ask_dollars")
            if yes_bid is None or yes_ask is None:
                continue

            try:
                yes_bid = float(yes_bid)
                yes_ask = float(yes_ask)
            except (ValueError, TypeError):
                continue

            if team not in team_round_prices:
                team_round_prices[team] = {}
            team_round_prices[team][round_part] = {"bid": yes_bid, "ask": yes_ask}

        # Pick the deepest round available for each team (prefer CHAMP > F4 > E8 > S16 > T2)
        for team, rounds in team_round_prices.items():
            best_round = None
            best_priority = 999
            for rnd, pri in round_priority.items():
                if rnd in rounds and pri < best_priority:
                    best_round = rnd
                    best_priority = pri
            if best_round is None:
                # Use whatever is available
                best_round = next(iter(rounds))

            prices = rounds[best_round]
            data.kalshi_prices[team] = {
                "yes_bid": prices["bid"],
                "yes_ask": prices["ask"],
                "round": best_round,
            }

    except Exception:
        pass  # Kalshi is optional


async def fetch_all_data(data: DashboardData):
    """Fetch positions, orderbooks, Kalshi, and compute fair values."""
    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {TOKEN}"},
        connector=connector,
    ) as session:
        api_url = _game_api_url(BASE_URL, GAME_ID)

        # Fetch account (positions + cash)
        try:
            async with session.get(f"{api_url}/account", ssl=False) as resp:
                acct = await resp.json()
                data.positions = acct.get("positions", {})
                data.cash = acct.get("cash", 0.0)
                data.margin = acct.get("margin", 0.0)
        except Exception as e:
            data.error = f"Account fetch failed: {e}"
            return

        # Fetch orderbooks
        try:
            async with session.get(f"{api_url}/orderbooks", ssl=False) as resp:
                books_raw = await resp.json()
                data.order_books = {}
                symbols = []
                for symbol, book in books_raw.items():
                    symbols.append(symbol)
                    bids = book.get("bids", {})
                    asks = book.get("asks", {})
                    best_bid = max((float(p) for p in bids.keys()), default=None) if bids else None
                    best_ask = min((float(p) for p in asks.keys()), default=None) if asks else None
                    data.order_books[symbol] = {"best_bid": best_bid, "best_ask": best_ask}

                # Match symbols to teams
                update_symbol_mapping(symbols)
                data.matched_symbols = dict(SYMBOL_TO_TEAM)
        except Exception as e:
            data.error = f"Orderbook fetch failed: {e}"
            return

        # Compute fair values and fetch Kalshi in parallel
        fv_task = asyncio.to_thread(compute_fair_values, 10000)
        kalshi_task = fetch_kalshi_prices(session, data)
        try:
            data.fair_values, _ = await asyncio.gather(fv_task, kalshi_task)
        except Exception as e:
            data.error = f"FV/Kalshi failed: {e}"

        data.last_update = time.time()
        data.error = ""


def bg_fetch_loop(data: DashboardData, stop_event: threading.Event):
    """Background thread: fetch data every REFRESH_INTERVAL seconds."""
    loop = asyncio.new_event_loop()
    while not stop_event.is_set():
        try:
            loop.run_until_complete(fetch_all_data(data))
        except Exception as e:
            data.error = str(e)
        stop_event.wait(REFRESH_INTERVAL)
    loop.close()


class DashboardApp:
    def __init__(self, root: tk.Tk, data: DashboardData):
        self.root = root
        self.data = data
        self.sort_col = "fair_value"
        self.sort_reverse = True
        self.root.title("NCAA Trading Bot Dashboard")
        self.root.geometry("1150x700")
        self.root.configure(bg="#1e1e1e")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
                        background="#2d2d2d", foreground="white",
                        fieldbackground="#2d2d2d", rowheight=22, font=("Consolas", 10))
        style.configure("Treeview.Heading",
                        background="#3c3c3c", foreground="white", font=("Consolas", 10, "bold"))
        style.map("Treeview", background=[("selected", "#404040")])

        # Top bar
        top = tk.Frame(root, bg="#1e1e1e")
        top.pack(fill=tk.X, padx=10, pady=5)

        self.cash_label = tk.Label(top, text="Cash: --", fg="#4ec9b0", bg="#1e1e1e",
                                   font=("Consolas", 13, "bold"))
        self.cash_label.pack(side=tk.LEFT, padx=15)

        self.margin_label = tk.Label(top, text="Margin: --", fg="#ce9178", bg="#1e1e1e",
                                     font=("Consolas", 13, "bold"))
        self.margin_label.pack(side=tk.LEFT, padx=15)

        self.total_label = tk.Label(top, text="Total: --", fg="#dcdcaa", bg="#1e1e1e",
                                    font=("Consolas", 13, "bold"))
        self.total_label.pack(side=tk.LEFT, padx=15)

        self.status_label = tk.Label(top, text="Loading...", fg="#808080", bg="#1e1e1e",
                                     font=("Consolas", 10))
        self.status_label.pack(side=tk.RIGHT, padx=15)

        # Columns config
        self.columns = ("team", "position", "fair_value", "best_bid", "best_ask", "spread",
                        "edge_bid", "edge_ask", "kalshi_bid", "kalshi_ask", "model_prob")
        self.col_labels = {
            "team": "Team", "position": "Pos", "fair_value": "Fair Value",
            "best_bid": "Best Bid", "best_ask": "Best Ask", "spread": "Spread",
            "edge_bid": "Sell Edge", "edge_ask": "Buy Edge",
            "kalshi_bid": "Kalshi Bid", "kalshi_ask": "Kalshi Ask",
            "model_prob": "Model Prob%",
        }
        col_widths = {"team": 140, "position": 55, "fair_value": 80, "best_bid": 72,
                      "best_ask": 72, "spread": 60, "edge_bid": 75, "edge_ask": 75,
                      "kalshi_bid": 78, "kalshi_ask": 78, "model_prob": 85}

        frame = tk.Frame(root, bg="#1e1e1e")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL)
        self.tree = ttk.Treeview(frame, columns=self.columns, show="headings",
                                  yscrollcommand=scrollbar.set)
        scrollbar.configure(command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

        for col in self.columns:
            label = self.col_labels[col]
            self.tree.heading(col, text=label, command=lambda c=col: self._on_sort(c))
            anchor = tk.W if col == "team" else tk.CENTER
            self.tree.column(col, width=col_widths.get(col, 80), anchor=anchor)

        # Store raw row data for sorting
        self.raw_rows = []
        self.refresh_ui()

    def _on_sort(self, col):
        """Handle column header click for sorting."""
        if self.sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col
            # Default: descending for numeric, ascending for team
            self.sort_reverse = (col != "team")
        self._repopulate_tree()

    def _sort_key(self, row_dict, col):
        """Extract a sortable value from the row dict."""
        val = row_dict.get(col)
        if val is None:
            return (1, 0)  # Nones sort last
        if col == "team":
            return (0, val.lower())
        return (0, val)

    def _repopulate_tree(self):
        """Sort raw_rows by current sort column and repopulate tree."""
        sorted_rows = sorted(
            self.raw_rows,
            key=lambda r: self._sort_key(r, self.sort_col),
            reverse=self.sort_reverse,
        )

        for item in self.tree.get_children():
            self.tree.delete(item)

        # Update heading labels with sort indicator
        for col in self.columns:
            label = self.col_labels[col]
            if col == self.sort_col:
                arrow = " \u25bc" if self.sort_reverse else " \u25b2"
                label += arrow
            self.tree.heading(col, text=label)

        for r in sorted_rows:
            pos = r.get("position", 0) or 0
            fv = r.get("fair_value", 0) or 0
            bb = r.get("best_bid")
            ba = r.get("best_ask")
            sp = r.get("spread")
            eb = r.get("edge_bid")
            ea = r.get("edge_ask")
            kb = r.get("kalshi_bid")
            ka = r.get("kalshi_ask")
            mp = r.get("model_prob", 0) or 0

            values = (
                r.get("team", ""),
                pos if pos != 0 else "",
                f"{fv:.2f}" if fv else "",
                f"{bb:.1f}" if bb is not None else "",
                f"{ba:.1f}" if ba is not None else "",
                f"{sp:.1f}" if sp is not None else "",
                f"{eb:+.1f}" if eb is not None else "",
                f"{ea:+.1f}" if ea is not None else "",
                f"{kb:.0%}" if kb is not None else "",
                f"{ka:.0%}" if ka is not None else "",
                f"{mp:.1f}%" if mp > 0 else "",
            )

            tag = ""
            if pos > 0:
                tag = "long"
            elif pos < 0:
                tag = "short"

            self.tree.insert("", tk.END, values=values, tags=(tag,))

        self.tree.tag_configure("long", foreground="#4ec9b0")
        self.tree.tag_configure("short", foreground="#f44747")

    def refresh_ui(self):
        """Refresh table from shared data."""
        self.cash_label.config(text=f"Cash: ${self.data.cash:,.0f}")
        self.margin_label.config(text=f"Margin: ${self.data.margin:,.0f}")
        total = self.data.cash + self.data.margin
        self.total_label.config(text=f"Total: ${total:,.0f}")

        if self.data.error:
            self.status_label.config(text=f"Error: {self.data.error}", fg="#f44747")
        elif self.data.last_update > 0:
            ago = int(time.time() - self.data.last_update)
            self.status_label.config(text=f"Updated {ago}s ago", fg="#608b4e")
        else:
            self.status_label.config(text="Loading...", fg="#808080")

        # Build raw rows
        all_teams = set()
        for team in self.data.fair_values:
            all_teams.add(team)
        for symbol, qty in self.data.positions.items():
            team = self.data.matched_symbols.get(symbol)
            if team:
                all_teams.add(team)

        self.raw_rows = []
        for team in all_teams:
            fv = self.data.fair_values.get(team, 0.0)
            symbol = TEAM_TO_SYMBOL.get(team)

            pos = 0
            if symbol and symbol in self.data.positions:
                pos = self.data.positions[symbol]

            best_bid = None
            best_ask = None
            if symbol and symbol in self.data.order_books:
                ob = self.data.order_books[symbol]
                best_bid = ob["best_bid"]
                best_ask = ob["best_ask"]

            spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
            edge_bid = (best_bid - fv) if (best_bid is not None and fv > 0) else None
            edge_ask = (fv - best_ask) if (best_ask is not None and fv > 0) else None

            kalshi = self.data.kalshi_prices.get(team, {})
            k_bid = kalshi.get("yes_bid")
            k_ask = kalshi.get("yes_ask")

            model_prob = (fv / 64.0 * 100) if fv > 0 else 0.0

            if fv < 0.01 and pos == 0:
                continue

            self.raw_rows.append({
                "team": team, "position": pos, "fair_value": fv,
                "best_bid": best_bid, "best_ask": best_ask, "spread": spread,
                "edge_bid": edge_bid, "edge_ask": edge_ask,
                "kalshi_bid": k_bid, "kalshi_ask": k_ask,
                "model_prob": model_prob,
            })

        self._repopulate_tree()
        self.root.after(2000, self.refresh_ui)


def main():
    data = DashboardData()
    stop_event = threading.Event()

    bg_thread = threading.Thread(target=bg_fetch_loop, args=(data, stop_event), daemon=True)
    bg_thread.start()

    root = tk.Tk()
    app = DashboardApp(root, data)

    def on_close():
        stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
