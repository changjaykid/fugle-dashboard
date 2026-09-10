"""Synthetic fixtures exercise source-time/phase boundaries; no live keys."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from stock_radar.fugle import FugleClient, FugleError, normalize_quote, microtime
from stock_radar.domain import TW, decide

NOW=datetime(2026,9,10,8,50,tzinfo=TW)
def micros(t): return int(t.timestamp()*1_000_000)
def inputs():
    return ({'symbol':'0050','name':'test','date':'2026-09-10','previousClose':100,'referencePrice':100,
      'isTrial':True,'lastTrial':{'price':99,'time':micros(NOW-timedelta(seconds=1))},
      'lastTrade':{'price':100,'time':micros(NOW-timedelta(days=1))},'lastUpdated':micros(NOW),
      'bids':[{'price':98,'size':20}],'asks':[{'price':99,'size':10}]},
      {'symbol':'0050','date':'2026-09-10','securityStatus':'NORMAL','securityType':'24',
       'isDisposition':False,'tradingCurrency':'TWD','limitUpPrice':110,'limitDownPrice':90,
       'previousClose':100,'referencePrice':100})

class TestNormalize(unittest.TestCase):
    def test_trial_and_source_time(self):
        q=normalize_quote(*inputs(),now=NOW)
        self.assertEqual(q['trial_price'],99);self.assertEqual(q['price'],99)
        self.assertTrue(q['is_trial']);self.assertFalse(q['halted'])
        self.assertEqual(q['as_of'],(NOW-timedelta(seconds=1)).isoformat())
    def test_historical_trial_does_not_set_phase(self):
        r,t=inputs();del r['isTrial']
        q=normalize_quote(r,t,now=NOW);self.assertIsNone(q['is_trial']);self.assertTrue(q['halted'])
    def test_current_trade_and_historical_trial_separate(self):
        r,t=inputs();r.pop('isTrial');r['isContinuous']=True;r['lastTrade']={'price':101,'time':micros(NOW)}
        q=normalize_quote(r,t,now=NOW);self.assertEqual(q['price'],101);self.assertEqual(q['trial_price'],99);self.assertFalse(q['is_trial'])
    def test_close_auction_not_labelled_premarket(self):
        r,t=inputs();r.pop('isTrial');r['isClose']=True;r['lastTrial']['time']=micros(NOW.replace(hour=13,minute=29))
        r['lastTrade']={'price':101,'time':micros(NOW.replace(hour=13,minute=30))};r['lastUpdated']=r['lastTrade']['time']
        q=normalize_quote(r,t,now=NOW.replace(hour=18));self.assertIsNone(q['trial_price']);self.assertFalse(q['is_trial'])
    def test_missing_microsecond_time_cannot_be_refreshed_by_fetch(self):
        r,t=inputs();r['lastUpdated']=int(NOW.timestamp())
        q=normalize_quote(r,t,now=NOW);self.assertIsNone(q['as_of']);self.assertTrue(q['halted'])
    def test_future_timestamp_blocked(self):
        r,t=inputs();r['lastUpdated']=micros(NOW+timedelta(seconds=1))
        self.assertTrue(normalize_quote(r,t,now=NOW)['halted'])
    def test_cached_old_price_stays_old(self):
        r,t=inputs();r['lastTrial']['time']=micros(NOW-timedelta(minutes=10))
        self.assertEqual(normalize_quote(r,t,now=NOW)['as_of'],(NOW-timedelta(minutes=10)).isoformat())
    def test_bond_and_currency_and_disposition(self):
        for field,value in [('securityType','32'),('tradingCurrency','USD'),('securityStatus','SUSPENDED'),('date','2026-09-09')]:
            r,t=inputs();t[field]=value;self.assertTrue(normalize_quote(r,t,now=NOW)['halted'])
        r,t=inputs();t['isDisposition']=True;self.assertTrue(normalize_quote(r,t,now=NOW)['disposition'])
    def test_malformed_book_and_numbers(self):
        r,t=inputs();r['bids']=[None,{'price':'inf','size':4},{'price':'98','size':'5'}]
        self.assertEqual(normalize_quote(r,t,now=NOW)['bids'],[{'price':98.0,'size':5.0}])
    def test_reference_conflict_blocked(self):
        r,t=inputs();t['referencePrice']=99
        self.assertTrue(normalize_quote(r,t,now=NOW)['halted'])
    def test_symbol_mismatch(self):
        r,t=inputs();t['symbol']='3661'
        with self.assertRaises(FugleError):normalize_quote(r,t,now=NOW)

class Response:
    def __init__(self,payload,status=200):self.payload=payload;self.status_code=status
    def json(self):return self.payload
class Session:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def get(self,*args,**kwargs):self.calls.append((args,kwargs));return next(self.responses)
    def close(self):pass

class TestClient(unittest.TestCase):
    def test_ticker_cached_daily_and_calls_throttled(self):
        r,t=inputs();s=Session([Response(t),Response(r),Response(r)]);waits=[]
        c=FugleClient('test-only',session=s,clock=lambda:0,sleep=waits.append)
        c.quote('0050',now=NOW);c.quote('0050',now=NOW)
        self.assertEqual(len(s.calls),3);self.assertEqual(waits,[1.1,1.1])
        self.assertFalse(s.calls[0][1]['allow_redirects'])
    def test_error_never_prints_key_or_response_body(self):
        c=FugleClient('secret-test',session=Session([Response({'message':'secret-test'},401)]))
        with self.assertRaises(FugleError) as e:c.quote('0050',now=NOW)
        self.assertIn('401',str(e.exception));self.assertNotIn('secret-test',str(e.exception))
    def test_symbol_cannot_inject_url(self):
        c=FugleClient('test',session=Session([]))
        with self.assertRaises(FugleError):c.quote('../config',now=NOW)

class TestBatch(unittest.TestCase):
    def test_batch_deduplicates_and_reuses_owned_client(self):
        from stock_radar.fugle import fetch_quotes
        from unittest.mock import Mock
        c=Mock();c.quote.return_value={'symbol':'0050'}
        self.assertEqual(fetch_quotes([{'symbol':'0050'},{'symbol':'0050'}], 'test',client=c),{'0050':{'symbol':'0050'}})
        c.quote.assert_called_once_with('0050');c.close.assert_not_called()
    def test_batch_rejects_over_quota_before_network(self):
        from stock_radar.fugle import fetch_quotes
        with self.assertRaises(FugleError):
            fetch_quotes([{'symbol':str(1000+i)} for i in range(31)], 'test')

if __name__=='__main__':unittest.main()
