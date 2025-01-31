import os
import logging
import logging.handlers
import threading
import time
from datetime import timedelta as td
import asyncio
from alpaca.trading.client import TradingClient
from . import trade
from . import io
from . import creek_signal as signal
from . import config as g

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s:%(levelname)s:%(name)s:%(message)s",
  handlers=[logging.handlers.WatchedFileHandler(os.environ.get("LOGFILE", "creek.log"))]
)
logger = logging.getLogger(__name__)
# Load trade objects including open trade objects
# test
trading_client = TradingClient(g.key, g.secret_key)
AVB = trading_client.get_asset('AVB')
AIRC = trading_client.get_asset('AIRC')
t = trade.Trade([AVB, AIRC], 0.9, 0.94)

io.load_config()
g.active_symbols = io.load_trades()
# Also sets g.cash, g.equity, g.positions
if not trade.account_ok(): sys.exit(1)
initial_equity = g.equity
trade.set_trade_size()
for symbol in g.active_symbols.keys():
  g.bars[symbol] = []
clock = signal.Clock()
while not clock.is_open: clock.rest()
s = threading.Thread(target=io.stock_wss, daemon=True)
s.start()
time.sleep(10) # to prevent circular import
a = threading.Thread(target=io.account_wss, daemon=True)
a.start()
time.sleep(2)
# Canceling open trades
signal.cancel_all()
asyncio.run(signal.resolve_positions())
time.sleep(2)
first_bar = False
while not first_bar:
  time.sleep(1)
  for symbol in g.active_symbols:
    if g.bars[symbol]: first_bar = True
while ((clock.next_close - clock.now()) >= td(minutes=1, seconds=58)):
  asyncio.run(signal.main(clock))
io.save()
io.report(initial_equity)