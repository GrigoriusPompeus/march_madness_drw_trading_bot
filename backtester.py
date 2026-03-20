import csv
from collections import defaultdict
import datetime
import math

# Trading parameters (from bot.py)
MIN_EDGE = 1.5              
MAX_POSITION = 80           
KELLY_FRACTION = 0.15       
SPREAD_LIMIT = 4.0          
MIN_PRICE = 0.5             
MAX_PRICE = 63.5            

class Backtester:
    def __init__(self, initial_cash=0.0):
        self.cash = initial_cash
        self.positions = defaultdict(int)
        self.trade_history = []
        self.pnl_history = []
        self.timestamps = []
        
    def run_backtest(self, filename="market_data.csv"):
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
                        fair_value = float(row['fair_value'])
                        spread = float(row['spread']) if row.get('spread') else None
                    except ValueError:
                        continue # Skip missing or invalid rows
                    
                    if best_bid is None or best_ask is None or fair_value is None:
                        continue
                    
                    if spread is not None and spread > SPREAD_LIMIT:
                        continue
                        
                    # Evaluate BUY conditions (Fair value > Best Ask)
                    if fair_value - best_ask > MIN_EDGE and best_ask <= MAX_PRICE:
                        edge = fair_value - best_ask
                        current_pos = self.positions[symbol]
                        
                        if current_pos < MAX_POSITION:
                            # Simplistic quantity: 1 contract per opportunity, up to max
                            qty = min(10, MAX_POSITION - current_pos) 
                            
                            cost = qty * best_ask
                            self.cash -= cost
                            self.positions[symbol] += qty
                            self.trade_history.append((row['timestamp'], symbol, "BUY", qty, best_ask, fair_value, edge))
                    
                    # Evaluate SELL conditions (Best Bid > Fair value)
                    elif best_bid - fair_value > MIN_EDGE and best_bid >= MIN_PRICE:
                        edge = best_bid - fair_value
                        current_pos = self.positions[symbol]
                        
                        if current_pos > -MAX_POSITION: # Allow shorting up to max position
                            qty = min(10, MAX_POSITION + current_pos)
                            
                            revenue = qty * best_bid
                            self.cash += revenue
                            self.positions[symbol] -= qty
                            self.trade_history.append((row['timestamp'], symbol, "SELL", qty, best_bid, fair_value, edge))
                    
                    count += 1
                    
                    # Compute MTM PnL occasionally
                    if count % 100 == 0:
                        self.record_pnl(row['timestamp'], fair_value)
                        
                print(f"Processed {count} market ticks.")
        except FileNotFoundError:
            print("Could not find the market data file.")
    
    def record_pnl(self, timestamp, current_fv=None):
        mtm = self.cash
        # Mark to market using the last known fair values (simplified)
        for symbol, qty in self.positions.items():
            # Ideally we'd track the ongoing fair value of each symbol, but let's approximate
            # or skip MTM for now and just print final cash. 
            pass # Skipping exact MTM in middle of loop without tracking all FVs
            
    def display_results(self):
        print("\n=== Backtest Results ===")
        print(f"Total Trades Executed: {len(self.trade_history)}")
        print(f"Final Cash (Unrealized positions not accounted): ${self.cash:.2f}")
        
        print("\nFinal Positions:")
        mtm_value = self.cash
        for symbol, qty in self.positions.items():
            if qty != 0:
                print(f"  {symbol}: {qty} contracts")
                # Using 0 for mark-to-market since game is over (or we assume an average price of 32 for unpriced contracts, but this is highly variable)
                
        print(f"\nNet PnL Approximation (Cash + Positions at 0): ${self.cash:.2f}")

if __name__ == "__main__":
    bt = Backtester()
    bt.run_backtest("market_data.csv")
    bt.display_results()