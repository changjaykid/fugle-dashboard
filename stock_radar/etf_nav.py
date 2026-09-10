"""Issuer-published closing NAV. Read literals only; never execute page JS.

Verified on Yuanta 0050 official product page, 2026-09-10. This deliberately
supports 0050 only until other issuer page contracts are checked. NAV is a
research input, NOT an automatic valuation anchor or a live iNAV quote.
"""
from datetime import datetime
import json
import re
import requests
from .domain import TW, positive

URL='https://www.yuantaetfs.com/product/detail/0050'


def parse_0050(html, *, now=None):
    now=(now or datetime.now(TW)).astimezone(TW)
    blocks=re.findall(r'fileLinkData:\{(.*?)\},tagList:',html,re.S)
    if len(blocks)!=1:
        raise ValueError('元大淨值頁面結構改變，未更新淨值')
    block=blocks[0]
    def literal(key):
        hits=re.findall(r'(?:^|,)'+key+r':("(?:[^"\\]|\\.)*")(?=,|$)',block)
        if len(hits)!=1:raise ValueError('元大淨值缺少唯一欄位 '+key)
        return json.loads(hits[0])
    name=literal('FUND_SH_NM')
    if not name.endswith('(0050)') or literal('FUND_CURRENCY')!='NTD':
        raise ValueError('淨值標的或幣別不一致')
    nav=positive(literal('NAV'))
    day=datetime.strptime(literal('NAV_DATE'),'%Y/%m/%d').date()
    if not nav or day>now.date():raise ValueError('淨值或日期無效')
    return {'symbol':'0050','name':name,'nav':nav,'nav_date':day.isoformat(),
            'currency':'TWD','nav_type':'official_close','source':'元大投信',
            'source_url':URL,'fetched_at':now.isoformat(),
            'age_calendar_days':(now.date()-day).days}


def fetch_0050(*,session=None,now=None):
    r=(session or requests).get(URL,timeout=(5,20))
    r.raise_for_status()
    return parse_0050(r.text,now=now)
