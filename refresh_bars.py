import os
import logging
import logging.handlers
import time
from datetime import datetime as dt
from datetime import timedelta as td
import pytz as tz
import pandas as pd
import glob
import sys
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass
from alpaca.data.live import StockDataStream
from alpaca.trading.stream import TradingStream
from alpaca.trading.models import Asset
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.common.exceptions import APIError
import shutil
import config as g

def ll(filename):
  with open(filename, 'rb') as f:
    try:  # catch OSError in case of a one line file 
        f.seek(-2, os.SEEK_END)
        while f.read(1) != b'\n':
            f.seek(-2, os.SEEK_CUR)
    except OSError:
        f.seek(0)
    last_line = f.readline().decode()
  return last_line

def fetch_bars(request):
  logger = logging.getLogger(__name__)
  try:
    bars = g.hclient.get_stock_bars(request)
    return bars.df
  except APIError as api_error:
    return pd.DataFrame()
  except AttributeError as error:
    logger.warning('Empty request')
    return pd.DataFrame()

def get_bars(symbol, tf, s):
  request = StockBarsRequest(
                          symbol_or_symbols=symbol,
                          timeframe=tf,
                          limit=None,
                          start=s,
                          end=dt.now()-td(minutes=20),
                          adjustment="split"
                     )
  return fetch_bars(request)

def refresh_bar(s, path, tf, error_counter):
  logger = logging.getLogger(__name__)
  if os.path.isfile(path):
    line = ll(path)
    try:
      lb_date = dt.fromisoformat(line.split(',')[1]).astimezone(tz.timezone('UTC'))
    except IndexError as e:
      logger.info('File is empty; pulling fresh bars')
      bars = get_bars(s,tf,dt(g.historical_year,1,1))
      logger.info('Writing bars')
      if not bars.empty: bars.to_csv(path)
      return
    bars = get_bars(s, tf, lb_date)
    if bars.empty:
      logger.error('Unable to refresh %s' % s)
      error_counter = True
      return
    if abs(bars.iloc[0].open - float(line.split(',')[2])) > 0.01:
      logger.info('Bar price discrepancy; full refresh needed')
      bars = get_bars(s, tf, dt(g.historical_year,1,1))
      logger.info('Writing bars')
      if not bars.empty: bars.to_csv(path)
    else:
      logger.info('Concatenating')
      bars[1:].to_csv(s + '-new.csv', header=False)
      with open(path[:-4] + '-temp.csv','wb') as wfd:
        for f in [path, s + '-new.csv']:
          with open(f,'rb') as fd:
            shutil.copyfileobj(fd, wfd)
      os.remove(s + '-new.csv')
      shutil.move(path[:-4] + '-temp.csv', path)
  else:
    logger.info('No record found; pulling fresh bars')
    bars = get_bars(s, tf, dt(g.historical_year,1,1))
    logger.info('Writing bars')
    if not bars.empty: bars.to_csv(path)

def refresh_bars(s,error_counter):
  refresh_bar(s, os.path.join(g.minute_bar_dir, s + '.csv'), TimeFrame.Minute, error_counter)
  refresh_bar(s, os.path.join(g.hour_bar_dir, s + '.csv'), TimeFrame.Hour, error_counter)
  return

def get_open_symbols():
  symbol_list = []
  path = os.path.join(g.root, 'open_trades/*.json')
  open_trade_list = glob.glob(path)
  for open_trade in open_trade_list:
    title = open_trade.split('/')[-1]
    title = title.split('.')[0]
    symbols = title.split('-')
    symbol_list.extend(symbols)
  return symbol_list

def get_shortable_equities():
  search_params = GetAssetsRequest(asset_class=AssetClass.US_EQUITY)
  assets = g.tclient.get_all_assets(search_params)
  symbols = []
  for a in assets:
    if a.tradable==True and a.shortable==True:
      symbols.append(a.symbol)
  return symbols

def sanity_check():
  request = StockBarsRequest(
                          symbol_or_symbols='AAPL',
                          timeframe=TimeFrame.Hour,
                          limit=None,
                          start=dt.now()-td(days=3),
                          end=dt.now()-td(days=2),
                          adjustment="split"
                     )
  bars = fetch_bars(request)
  s = bars.to_csv()
  s = s.split('\n')[0]
  if s == 'symbol,timestamp,open,high,low,close,volume,trade_count,vwap':
    return 1
  else:
    logger = logging.getLogger(__name__)
    logger.error('Column is insane: %s' % s)
    return 0

def main():
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(name)s:%(message)s",
    handlers=[logging.handlers.WatchedFileHandler(os.environ.get("LOGFILE", "creek-fetch_bars.log"))]
  )
  logger = logging.getLogger(__name__)
  if not sanity_check(): return
  symbol_list = list(set(get_shortable_equities() + get_open_symbols()))
  error_counter = False
  for i in range(len(symbol_list)):
    logger.info('Refreshing %s, %s/%s' %
                (symbol_list[i], i+1, len(symbol_list)))
    refresh_bars(symbol_list[i],error_counter)
  if error_counter:
    logger.warn('Some symbols were not updated')
  return

if __name__ == '__main__':
  main()