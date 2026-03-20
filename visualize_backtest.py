import csv
from collections import defaultdict
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
from model import compute_fair_values, SYMBOL_TO_TEAM

# Trading parameters (from bot.py)
MIN_EDGE = 1.5
MAX_POSITION = 80
SPREAD_LIMIT = 4.0
MIN_PRICE = 0.5
MAX_PRICE = 63.5

class InteractiveBacktester:
    def __init__(self, initial_cash=0.0):
        self.cash = initial_cash
        self.positions = defaultdict(int)
        self.latest_fvs = {}
        self.trade_history = []
        self.pnl_history = []
        
    def run_backtest(self, filename="market_data.csv"):
        print("Recomputing NEW fair values from updated model...")
        new_fvs = compute_fair_values(n_sims=50000)
        # Map TEAM names to SYMBOL names if needed, or assume they align
        # If compute_fair_values returns team names, we might need TEAM_TO_SYMBOL
        from model import TEAM_TO_SYMBOL
        self.new_fvs_symbol = {TEAM_TO_SYMBOL.get(team, team): fv for team, fv in new_fvs.items()}
        print("Done computing fair values. Running backtest...")
        
        print(f"Reading market data from {filename}...")
        try:
            with open(filename, 'r') as f:
                reader = csv.DictReader(f)
                
                count = 0
                for row in reader:
                    symbol = row.get('symbol')
                    team = row.get('team')
                    try:
                        best_bid = float(row['best_bid']) if row.get('best_bid') else None
                        best_ask = float(row['best_ask']) if row.get('best_ask') else None
                        
                        # USE THE NEW FAIR VALUE INSTEAD OF THE ONE IN THE CSV
                        fair_value = self.new_fvs_symbol.get(symbol)
                        if fair_value is None:
                            continue
                            
                        spread = float(row['spread']) if row.get('spread') else None
                    except ValueError:
                        continue # Skip missing or invalid rows
                    
                    if best_bid is None or best_ask is None or fair_value is None:
                        continue
                    
                    # Update latest fair value for active MTM tracking
                    self.latest_fvs[symbol] = fair_value
                    
                    if spread is not None and spread > SPREAD_LIMIT:
                        continue
                        
                    trade_happened = False
                    
                    # Evaluate BUY conditions
                    if fair_value - best_ask > MIN_EDGE and best_ask <= MAX_PRICE:
                        edge = fair_value - best_ask
                        current_pos = self.positions[symbol]
                        
                        if current_pos < MAX_POSITION:
                            qty = min(10, MAX_POSITION - current_pos)
                            cost = qty * best_ask
                            self.cash -= cost
                            self.positions[symbol] += qty
                            self.trade_history.append({
                                'timestamp': row['timestamp'], 'symbol': symbol, 
                                'side': 'BUY', 'qty': qty, 'price': best_ask, 
                                'fair_value': fair_value, 'edge': edge
                            })
                            trade_happened = True
                            
                    # Evaluate SELL conditions
                    elif best_bid - fair_value > MIN_EDGE and best_bid >= MIN_PRICE:
                        edge = best_bid - fair_value
                        current_pos = self.positions[symbol]
                        
                        if current_pos > -MAX_POSITION:
                            qty = min(10, MAX_POSITION + current_pos)
                            revenue = qty * best_bid
                            self.cash += revenue
                            self.positions[symbol] -= qty
                            self.trade_history.append({
                                'timestamp': row['timestamp'], 'symbol': symbol, 
                                'side': 'SELL', 'qty': qty, 'price': best_bid, 
                                'fair_value': fair_value, 'edge': edge
                            })
                            trade_happened = True
                    
                    # Compute Mark-to-Market PnL every 100 ticks or when a trade happens
                    if trade_happened or count % 100 == 0:
                        mtm = self.cash + sum(self.positions[s] * self.latest_fvs.get(s, 0) for s in self.positions if self.positions[s] != 0)
                        self.pnl_history.append({
                            'timestamp': row['timestamp'],
                            'pnl': mtm
                        })
                        
                    count += 1
                print(f"Processed {count} market ticks. Generated {len(self.trade_history)} trades.")
        except FileNotFoundError:
            print(f"Could not find the {filename} file.")

    def plot_results(self):
        print("\nGenerating interactive visualization...")
        # Create DataFrame for PnL
        df_pnl = pd.DataFrame(self.pnl_history)
        # Ensure timestamp is datetime
        df_pnl['timestamp'] = pd.to_datetime(df_pnl['timestamp'])
        
        # Plotly figure
        fig = px.line(df_pnl, x='timestamp', y='pnl', title='Backtester Performance: Mark-to-Market PnL Over Time')
        fig.update_layout(yaxis_title="Unrealized + Realized PnL ($)", xaxis_title="Time")
        
        # Overlay Trade Markers
        if self.trade_history:
            df_trades = pd.DataFrame(self.trade_history)
            df_trades['timestamp'] = pd.to_datetime(df_trades['timestamp'])
            
            # Merge trades with nearest PnL to get Y coordinate for plotting directly on the line
            df_trades = pd.merge_asof(df_trades.sort_values('timestamp'), df_pnl.sort_values('timestamp'), on='timestamp', direction='nearest')
            
            # Separate buys and sells
            buys = df_trades[df_trades['side'] == 'BUY']
            sells = df_trades[df_trades['side'] == 'SELL']
            
            fig.add_trace(go.Scatter(
                x=buys['timestamp'], y=buys['pnl'],
                mode='markers', name='BUY',
                marker=dict(color='green', symbol='triangle-up', size=8),
                hovertext=buys['symbol'] + " | Qty: " + buys['qty'].astype(str) + " @ $" + buys['price'].astype(str)
            ))
            fig.add_trace(go.Scatter(
                x=sells['timestamp'], y=sells['pnl'],
                mode='markers', name='SELL',
                marker=dict(color='red', symbol='triangle-down', size=8),
                hovertext=sells['symbol'] + " | Qty: " + sells['qty'].astype(str) + " @ $" + sells['price'].astype(str)
            ))
        
        output_file = os.path.abspath('backtest_visual.html')
        fig.write_html(output_file)
        print(f"Saved interactive plot to {output_file}")
        
        # Open in default browser
        import webbrowser
        webbrowser.open('file://' + output_file)

if __name__ == "__main__":
    bt = InteractiveBacktester()
    bt.run_backtest("market_data.csv")
    bt.plot_results()
