import logging
import pandas as pd
import os
import asyncio
import glob
from datetime import datetime as dt
from datetime import timedelta as td
import pytz as tz
from fractions import Fraction
import math
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
import tensorflow.compat.v2 as tf
tf.enable_v2_behavior()
import tensorflow_probability as tfp
tfd = tfp.distributions
from tensorflow.python.framework import errors_impl as tf_errors
import numpy as np
from alpaca.trading.requests import LimitOrderRequest
from . import config as g

# Things we want for our trade class:
# have a status
# be able to load and save itself
# have a hedge symbol
# record prices
# contain tensorflow model information
# record minute bars for as long as it's open

class Trade:
  """
  Trade class.
  Attributes:
    _model: contains tensorflow model
    _has_model (bool): whether model weights have been successfully
                       loaded
    _status: 'uninitialized, ''disabled', 'open', 'closed', 'opening', 
             'closing'.
             'closed' means ready to trade. 'open' mean a position has 
             successfully been opened and the trade is on.
  """

  def _LoadWeights(self):
    logger = logging.getLogger(__name__)
    negloglik = lambda y, rv_y: -rv_y.log_prob(y)
    self._model = tf.keras.Sequential([
    tf.keras.layers.Dense(1 + 1),
    tfp.layers.DistributionLambda(
      lambda t: tfd.Normal(loc=t[..., :1],
                scale=1e-3 + tf.math.softplus(0.05 * t[...,1:]))),
    ])
    self._model.compile(
                optimizer=tf.optimizers.Adam(learning_rate=0.01),
                loss=negloglik)
    try:
      self._model.load_weights('%s/checkpoints/%s' % 
                               (g.root,self._title)).expect_partial()
      self._has_model = True
      return 1
    except tf_errors.NotFoundError as e:
      logger.error(e)
      self._has_model = False
      self._status = 'disabled'
      return 0

  def __init__(self, symbol_list, pearson, pearson_historical):
    self._status = 'uninitialized'
    assert len(symbol_list) == 2
    self._symbols = symbol_list
    assert type(pearson) is float
    assert type(pearson_historical) is float
    self._pearson = pearson
    self._pearson_historical = pearson_historical
    self._title = self._symbols[0].symbol + '-' + self._symbols[1].symbol
    if not self._LoadWeights(): return
    if not self._symbols[0].tradable or not self._symbols[1].tradable:
      self._status = 'disabled'
    if not self._symbols[0].shortable or not self._symbols[1].shortable:
      self._status = 'disabled'
    self._sigma_series = pd.Series(dtype=np.float64)
    self._opened = None # To be set in pytz timezone US/Eastern
    # ...
    self._status = 'closed'

  def refresh_open(self, symbol_list):
    assert len(symbol_list) == 2
    self._symbols = symbol_list
    if not self._symbols[0].tradable or not self._symbols[1].tradable:
      self._status = 'disabled'
      return 0
    else: return 1
  
  def status(self): return self._status
  def pearson(self): return self._pearson
  def title(self): return self._title

  def _mean(self, x):
    val = np.array([[x]], dtype=np.float32)
    return float(np.squeeze(self._model(val).mean().numpy()))

  def _stddev(self, x):
    val = np.array([[x]], dtype=np.float32)
    return float(np.squeeze(self._model(val).stddev().numpy()))

  def _sigma(self, x, y):
    return abs(y - self._mean(x)) / self._stddev(x)

  def _stddev_x(self, x):
    xpoints = np.array([[x]], [2 * x]], dtype='float32')
    m = self._model(points)
    ypoints = np.squeeze(m.mean().numpy)
    ydelta = self._stddev(x)
    return ( ydelta  / (ypoints[1] - ypoints[0]) ) * x 

  def append_bar(self):
    if (not g.bars[self._symbols[0].symbol] or
        not g.bars[self._symbols[1].symbol]): return
    new_bars = (g.bars[self._symbols[0].symbol][-1],
                g.bars[self._symbols[1].symbol][-1])
    time = max(new_bars[0].timestamp, new_bars[1].timestamp)
    if (len(self._sigma_series) > 0) and (
     (time.astimezone(tz.timezone('US/Eastern')) - 
     self._sigma_series.index[-1].astimezone(tz.timezone('US/Eastern')))
     < td(seconds=50)): return # Nothing new
    sigma = self._sigma(new_bars[0].vwap, new_bars[1].vwap)
    s = pd.Series({time,sigma})
    self._sigma_series = pd.concat([self._sigma_series,s])

  def open_signal(self, clock):
    '''
    Returns a 4-tuple with the
    - True/False whether to open
    - deviation as a fraction of the standard deviation
    - the symbol to go long
    - the symbol to go short
    '''
    if len(self._sigma_series) == 0: return 0, None, None, None
    else:
      sigma = self._sigma_series[-1]
      if sigma > g.TO_OPEN_SIGNAL:
        logger = logging.getLogger(__name__)
        x = g.bars[self._symbols[0].symbol][-1].vwap
        y = g.bars[self._symbols[1].symbol][-1].vwap
        if y > self._mean(x):
          return (1, sigma, self._symbols[0].symbol, 
                            self._symbols[1].symbol)
          logger.info('%s sigma = %s, long %s short %s' % 
                      self._title, sigma, self._symbols[0].symbol, 
                                          self._symbols[1].symbol)
        else:
          return (1, sigma, self._symbols[1].symbol, 
                            self._symbols[0].symbol)
          logger.info('%s sigma = %s, long %s short %s' % 
                      self._title, sigma, self._symbols[1].symbol, 
                                          self._symbols[0].symbol)
      else: return 0, None, None, None

  def close_signal(self, clock):
    logger = logging.getLogger(__name__)
    if len(self._sigma_series) == 0:
      logger.error('%s is open but has no sigma series' % self._title)
      return 0
    sigma = self._sigma_series[-1]
    time = self._sigma_series.index[-1]
    delta = clock.now() - self._opened
    if sigma < 0.25: return 1
    elif sigma < 0.5 and delta > td(weeks=1): return 1
    elif sigma < 1 and delta > td(weeks=2): return 1
    elif sigma < 2 and delta > td(weels=3): return 1
    else: return 0

  async def try_close(self, latest_bar):
    pass

  async def try_open(self, latest_quote, latest_trade):
    logger = logging.getLogger(__name__)
    price = (latest_trade[self._symbols[0].symbol].price,
             latest_trade[self._symbols[1].symbol].price)
    if price[0] > g.trade_size / 2
      logger.info('Passing on %s as one share of %s costs %s, whereas the max trade size is %s' % (self._title, self._symbols[0].symbol, price[0], g.trade_size))
      return 0
    if price[1] > g.trade_size / 2:
      logger.info('Passing on %s as one share of %s costs %s, whereas the max trade size is %s' % (self._title, self._symbols[1].symbol, price[1], g.trade_size))
      return 0
    sigma = self._sigma(price[0], price[1])
    if sigma < g.TO_OPEN_SIGNAL: return 0
    bid_ask = bid_ask(latest_quote, self._symbols)
    stddev = self._stddev(price[0])
    if stddev < 10 * bid_ask[0]:
      logger.info('Passing on %s as bid-ask spread for %s = %s while stddev = %s' % (self._title, self._symbols[0].symbol, bid_ask[0], stddev))
      return 0
    stddev_x = self._stddev_x(price[0]) # signed float
    if abs(stddev_x) < 10 * bid_ask[1]:
      logger.info('Passing on %s as bid-ask spread for %s = %s while |stddev_x| = %s' % (self._title, self._symbols[1].symbol, bid_ask[1], abs(stddev_x)))
      return 0

    mean = self._mean(price[0])
    if price[1] > mean:
      to_long = 0
      to_short = 1
    else:
      to_long = 1
      to_short = 0
    if self._symbols[to_long].fractionable:
      short_cushion = stddev / g.SIGMA_CUSHION if to_short else abs(stddev_x) / g.SIGMA_CUSHION
      short_limit = price[to_short] - min(bid_ask(price[to_short]),
                                          short_cushion)
      short_request = LimitOrderRequest(
                      symbol = self._symbols[to_short],
                      qty = math.floor((g.trade_size/2) / short_limit),
                      side = 'sell',
                      time_in_force = 'day',
                      client_order_id = self._title,
                      limit_price = short_limit
                      )
      g.tclient.submit_order(short_request)
    if price[1] >= price[0]:
      expensive = 1
      cheap = 0
    else:
      expensive = 0
      cheap = 1
    mm = min_max(Fraction(price[cheap]/price[expensive]),
                 math.floor((g.trade_size/2) / price[cheap]))
    if cheap == to_short: # want denominator big; i.e. lower bound
      shares_to_short = mm[0][1]
      shares_to_long = mm[0][0]
    else:  # want demoninator smaller; i.e. upper bound
      shares_to_long = mm[1][1]
      shares_to_short = mm[1][0]
    if (shares_to_short * price[to_short] 
        - shares_to_long * price[to_long]) < 0:
      logger.error('Long position is larger than short position')
      return 0
    return 0
  
  # To initialize already-open trades
  def open_init(dict):
    # ...
    self._status = 'open'
    pass

# See https://github.com/python/cpython/blob/3.10/Lib/fractions.py
def min_max(fraction, max_denominator):
  p0, q0, p1, q1 = 0, 1, 1, 0
  n, d = fraction.numerator, fraction.denominator
  while True:
    a = n//d
    q2 = q0+a*q1
    if q2 > max_denominator: break
    p0, q0, p1, q1 = p1, q1, p0+a*p1, q2
    n, d = d, n-a*d
  k = (max_denominator-q0)//q1
  bound1 = (p0+k*p1, q0+k*q1) # lower bound
  bound2 = (p1, q1) # upper bound
  return bound1, bound2

def bid_ask(latest_quote, symbols):
  ba = []
  assert len(symbols) == 2
  for s in symbols:
    ba.append(latest_quote[s.symbol].ask_price - 
              latest_quote[s.symbol].bid_price)
  return ba[0], ba[1]

def equity(account): return max(account.equity - g.EXCESS_CAPITAL,1)

def cash(account): return max(account.cash - g.EXCESS_CAPITAL,0)

def account_ok():
  logger = logging.getLogger(__name__)
  account = g.tclient.get_account()
  if account.trading_blocked:
    logger.error('Trading blocked, exiting')
    return 0
  if account.account_blocked:
    logger.error('Account blocked, exiting')
    return 0
  if trade_suspended_by_user:
    logger.error('Trade suspended by user, exiting')
    return 0
  if not account.shorting_enabled:
    logger.error('Shorting disabled, exiting')
    return 0
  g.equity = equity(account)
  g.cash = cash(account)
  g.positions = g.tclient.get_all_positions()
  return 1

def set_trade_size():
  logger = logging.getLogger(__name__)
  g.trade_size = g.equity * g.MAX_TRADE_SIZE
  logger.info('trade_size = %s' % g.trade_size)
  return