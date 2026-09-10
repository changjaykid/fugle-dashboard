"""Fugle REST adapter; official v1.0 quote/ticker contract, verified 2026-09-10.

Credentials stay in caller-owned private configuration. Requests are limited to
~54/min per shared client, below the free 60/min quota. Run only one collector
per account; independent processes must share the scheduler's process lock.
A lastTrial object is historical information, not evidence of an active trial.
"""
from datetime import datetime, time as daytime
import threading
import time
import re

import requests
from .domain import TW, positive

BASE = 'https://api.fugle.tw/marketdata/v1.0/stock/'


class FugleError(RuntimeError):
    pass


def microtime(value):
    n = positive(value)
    if n is None or not 10**14 < n < 4*10**15:
        return None
    try:
        return datetime.fromtimestamp(n / 1000000, TW)
    except (OverflowError, OSError, ValueError):
        return None


def levels(rows):
    if not isinstance(rows, list):
        return []
    result=[]
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        price, size=positive(row.get('price')),positive(row.get('size'))
        if price is not None and size is not None:
            result.append({'price':price,'size':size})
    return result


def normalize_quote(raw, ticker, *, now=None):
    now=(now or datetime.now(TW)).astimezone(TW)
    if not isinstance(raw, dict) or not isinstance(ticker, dict):
        raise FugleError('Fugle 回應格式錯誤')
    symbol=raw.get('symbol')
    if not symbol or symbol!=ticker.get('symbol'):
        raise FugleError('Fugle 報價與標的資料不一致')
    trial=raw.get('lastTrial') or {}
    trade=raw.get('lastTrade') or {}
    if not isinstance(trial,dict) or not isinstance(trade,dict):
        raise FugleError('Fugle 成交或試撮資料格式錯誤')
    updated=microtime(raw.get('lastUpdated'))
    trial_at=microtime(trial.get('time'))
    trade_at=microtime(trade.get('time'))
    phase=True if raw.get('isTrial') is True else (
        False if raw.get('isTrial') is False or any(raw.get(k) is True for k in ('isOpen','isContinuous','isClose')) else None)
    price_at=trial_at if phase is True else trade_at if phase is False else None
    source_at=min(price_at, updated) if price_at and updated else None
    issues=[]
    if raw.get('date')!=ticker.get('date'):
        issues.append('報價與交易限制日期不一致')
    if ticker.get('tradingCurrency')!='TWD':
        issues.append('尚不支援此交易幣別')
    if ticker.get('securityStatus')!='NORMAL':
        issues.append('交易狀態未確認正常')
    if ticker.get('securityType') not in ('01','24'):
        issues.append('此證券類別不適用 V1 掛價')
    if not source_at or not updated or source_at>now or updated>now:
        issues.append('來源行情時間缺失或異常')
    if phase is None:
        issues.append('缺少明確交易階段旗標')
    for field in ('previousClose','referencePrice'):
        a,b=positive(raw.get(field)),positive(ticker.get(field))
        if a and b and abs(a-b)>0.000001:
            issues.append(field+'來源不一致')
    halt=raw.get('tradingHalt') or {}
    if not isinstance(halt, dict):
        issues.append('暫停交易資料格式異常');halt={}
    pretrial=bool(trial_at and trial_at.date().isoformat()==raw.get('date') and
                  daytime(8,30)<=trial_at.time().replace(tzinfo=None)<daytime(9))
    return {
        'symbol':symbol,'name':raw.get('name'),'trade_date':raw.get('date'),
        'as_of':source_at.isoformat() if source_at else None,
        'fetched_at':now.isoformat(), 'is_trial':phase,
        'price':positive(trial.get('price') if phase is True else trade.get('price')),
        'last_trade_price':positive(trade.get('price')),
        'trial_price':positive(trial.get('price')) if pretrial else None,
        'trial_as_of':trial_at.isoformat() if pretrial else None,
        'previous_close':positive(raw.get('previousClose')),
        'reference_price':positive(raw.get('referencePrice')),
        'limit_up':positive(ticker.get('limitUpPrice')),
        'limit_down':positive(ticker.get('limitDownPrice')),
        'book_as_of':updated.isoformat() if updated else None,
        'bids':levels(raw.get('bids')), 'asks':levels(raw.get('asks')),
        'halted':bool(issues) or halt.get('isHalted') is True or any(raw.get(k) is True for k in ('isDelayedOpen','isDelayedClose','isLimitUpHalt','isLimitDownHalt')),
        'disposition':ticker.get('isDisposition') is not False,
        'security_status':ticker.get('securityStatus'),
        'security_type':ticker.get('securityType'),
        'currency':ticker.get('tradingCurrency'),
        'issues':issues,'is_close':raw.get('isClose') is True,
        'source':'Fugle REST','source_url':BASE+'intraday/quote/'+symbol,
    }


class FugleClient:
    def __init__(self, api_key, *, session=None, clock=time.monotonic, sleep=time.sleep):
        if not isinstance(api_key,str) or not api_key.strip() or '\n' in api_key or '\r' in api_key:
            raise FugleError('尚未配置有效 Fugle 金鑰')
        self._key=api_key.strip()
        self._session=session or requests.Session()
        self._clock=clock;self._sleep=sleep;self._last=None
        self._lock=threading.Lock();self._tickers={}

    def _get(self, endpoint, symbol):
        if not isinstance(symbol,str) or not re.fullmatch(r'[A-Za-z0-9]{4,10}',symbol):
            raise FugleError('無效股票代號')
        with self._lock:
            if self._last is not None:
                self._sleep(max(0,1.1-(self._clock()-self._last)))
            self._last=self._clock()
            try:
                response=self._session.get(BASE+'intraday/'+endpoint+'/'+symbol,
                    headers={'X-API-KEY':self._key},timeout=(5,15),allow_redirects=False)
            except requests.RequestException:
                raise FugleError('Fugle 連線失敗；未更新行情') from None
        if response.status_code!=200:
            # Never include a response body, request headers or original exception.
            raise FugleError(f'Fugle HTTP {response.status_code}；未更新行情')
        try:
            result=response.json()
        except (ValueError,TypeError):
            raise FugleError('Fugle 非有效 JSON 回應') from None
        if not isinstance(result,dict) or result.get('symbol')!=symbol:
            raise FugleError('Fugle 回應標的不一致')
        return result

    def quote(self, symbol, *, now=None):
        injected_now=now
        now=(now or datetime.now(TW)).astimezone(TW)
        cache_key=(symbol,now.date().isoformat())
        if cache_key not in self._tickers:
            self._tickers[cache_key]=self._get('ticker',symbol)
        raw=self._get('quote',symbol)
        # Use actual receive time in live operation; injected time is for replay tests.
        return normalize_quote(raw,self._tickers[cache_key],now=injected_now or datetime.now(TW))

    def close(self):
        self._session.close()


def fetch_quotes(instruments, api_key, *, client=None, max_symbols=30):
    """One bounded candidate batch. Reuse client across polling rounds.

    This is NOT a full-market realtime scanner. First pass needs two
    requests/symbol; 30 candidates fit roughly 66 seconds at free quota.
    Failure propagates; callers must record unhealthy state, never stamp
    old data as fresh or mistake a fallback feed for confirmed trial data.
    """
    symbols=list(dict.fromkeys(i['symbol'] for i in instruments))
    if len(symbols)>max_symbols:
        raise FugleError(f'候選數 {len(symbols)} 超過單批上限 {max_symbols}；請先依已核准估值篩選')
    owned=client is None
    client=client or FugleClient(api_key)
    try:
        return {s:client.quote(s) for s in symbols}
    finally:
        if owned:
            client.close()
