import logging
import logging.handlers
import os
import pandas as pd

handler = logging.handlers.WatchedFileHandler(
    os.environ.get("LOGFILE", "tf.log"))
formatter = logging.Formatter("%(asctime)s:%(levelname)s:%(name)s:%(message)s")
handler.setFormatter(formatter)
logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOGLEVEL", "INFO"))
logger.addHandler(handler)

p = pd.DataFrame()
indices = []
dev_directory = '/mnt/disks/creek-1/tf/dev'

def get_frame(row):
  global p
  global indices
  symbol1 = row['symbol1']
  symbol2 = row['symbol2']
  title = symbol1 + '-' + symbol2
  frame = pd.read_csv('%s/%s_dev.csv' % (dev_directory,title))
  assert not frame.empty
  frame_length = len(frame)
  frame[title] = frame['dev']
  frame = frame.drop(columns=['vwap_1','vwap_2','mean','stddev','dev'],axis=1)
  frame.set_index('timestamp', inplace=True)
  frame.index = pd.to_datetime(frame.index)
  if p.empty:
    p = frame
  else:
    p = p.merge(frame, how='outer', on='timestamp', suffixes=(None,None))
    indices.append(title)
  return

def summarize(row):
  counter = 0
  for i in indices:
    if row[i] >= 2: counter += 1
  return counter

def main():
  global p
  pearson = pd.read_csv('pearson.csv')
  pearson.apply(get_frame, axis=1)
  logger.info('Summarizing')
  p['summary'] = p.apply(lambda row: summarize(row), axis=1)
  # The below file is quite large and the information it contains is
  # not so critical so we omit it.
  # p.to_csv('list_dev.csv')
  p = p['summary']
  # Now the point is that we want to bin by hour ('H') or by day ('D')
  p = p.resample('H').sum()
  p.to_csv('summary_dev_hour.csv')
  p = p.resample('D').sum()
  p.to_csv('summary_dev_day.csv')
  logger.info('Done')
  return

if __name__ == '__main__':
  main()