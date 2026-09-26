import json,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar import research_llm as rl

NOW=datetime(2026,9,29,18,10,tzinfo=TW)
REPLY={'description':'伺服器代工','thesis':'本益比 14.3 低於一年常態 17.4，便宜約 17%，獲利支撐評價','why_now':'價格已進入買進區，等法人賣壓改善',
       'catalysts':['月營收年增是否延續'],'risks':['代工毛利偏低','法人仍在賣超'],'invalidation':['跌破支撐 77.6 元']}

def valuation(now,**over):
    v={'sweet':60.0,'add':70.0,'buy':82.8,'fair':97.5,'method':'pe_band','reason':'本益比 14.38 × 一年中位數 17.42',
       'inputs':{'pe_today':14.38,'fair_pe':17.42},'thesis':'t','as_of':(now-timedelta(hours=2)).isoformat(),
       'valid_until':(now+timedelta(days=3)).isoformat(),'evidence_reviewed':True,'review_mode':'rule-engine-v1',
       'sources':[{'url':'https://example.com/a','as_of':(now-timedelta(hours=2)).isoformat()}]}
    v.update(over);return v

class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.s=Store(Path(self.dir.name)/'t.db')
        self.s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
        self.s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{}})
        self.s.upsert_instruments([{'symbol':'3706','name':'神達','kind':'stock','market':'TSE','industry_code':'電腦及週邊設備業'},
                                   {'symbol':'0050','name':'元大台灣50','kind':'etf_equity','market':'TSE'}])
        for sym in ('3706','0050'):
            method={'0050':'etf_band'}.get(sym,'pe_band')
            self.s.publish_rule_valuation(self.s.propose(sym,valuation(NOW,method=method),now=NOW),now=NOW)
            self.s.set_fact(sym,'public_close',{'close':80.5,'trade_date':'2026-09-29'})
            self.s.set_fact(sym,'technical',{'as_of':'2026-09-29','flow_as_of':'2026-09-29','support':77.6,'supports':[77.6,79.0],
                                             'low_60d':77.6,'high_60d':95.8,'ma20':82.0,'resistance':95.8,
                                             'foreign_5d_lots':-1200.0,'trust_5d_lots':-50.0,'requires_stabilization':False})
            self.s.set_fact(sym,'risk',{'cleared':True,'events':[],'checked_at':(NOW-timedelta(hours=2)).isoformat()})
            self.s.set_fact(sym,'financials',{'income':{'eps_basic':2.58,'year_roc':'115','quarter':'2'},
                                              'revenue':{'yoy_pct':119.9,'period_roc_ym':'11508'}})
    def tearDown(self):self.s.close();self.dir.cleanup()

class SelectionTests(Fixture):
    def test_only_unresearched_actionable_stocks_are_chosen(self):
        picked=[i['symbol'] for i in rl.candidates(self.s,NOW)]
        self.assertEqual(picked,['3706'])  # the ETF follows the ETF rule and needs no company research
        rl.run(self.s,NOW,caller=lambda request:{'status':'ok','text':json.dumps(REPLY,ensure_ascii=False),
                                                 'usage':[{'provider':'anthropic','model':'claude-sonnet-5','estimated_cost_usd':0.01}]})
        self.assertEqual(rl.candidates(self.s,NOW),[])  # already researched, so the budget moves on

class ResearchTests(Fixture):
    def caller(self,reply=None,capture=None):
        def call(request):
            if capture is not None:capture.append(request)
            return reply or {'status':'ok','text':json.dumps(REPLY,ensure_ascii=False),
                             'usage':[{'provider':'anthropic','model':'claude-sonnet-5','estimated_cost_usd':0.012}]}
        return call

    def test_stored_research_unlocks_recommendation_and_keeps_provenance(self):
        from stock_radar.research_brief import researched
        captured=[]
        self.assertEqual(rl.run(self.s,NOW,caller=self.caller(capture=captured))[0][1],'researched')
        detail=self.s.fact('3706','research_detail')
        self.assertEqual(detail['thesis'],REPLY['thesis'])
        self.assertEqual(detail['risks'],REPLY['risks'])
        self.assertEqual((detail['provider'],detail['model'],detail['research_mode']),('anthropic','claude-sonnet-5','llm-v1'))
        self.assertTrue(researched(self.s,{'symbol':'3706','kind':'stock'},NOW))
        self.assertFalse(researched(self.s,{'symbol':'3706','kind':'stock'},NOW+timedelta(days=rl.VALID_DAYS+1)))
        self.assertIn('14.38',captured[0]['prompt'])  # the verified figures are what the model may use
        self.assertIn('不得引入任何其他數字',captured[0]['prompt'])

    def test_rounded_and_derived_figures_are_accepted(self):
        self.assertEqual(rl.run(self.s,NOW,caller=self.caller())[0][1],'researched')  # 14.3 / 17.4 / 17%

    def test_reply_inventing_a_figure_is_rejected(self):
        bad=dict(REPLY,thesis='目標價 150 元，市佔率 45%')
        reply={'status':'ok','text':json.dumps(bad,ensure_ascii=False),'usage':[{}]}
        result=rl.run(self.s,NOW,caller=self.caller(reply))
        self.assertEqual(result[0][1],'error')
        self.assertIn('unverified_numbers',result[0][2])
        self.assertEqual(self.s.fact('3706','research_detail'),{})

    def test_reply_breaking_length_or_count_limits_is_rejected(self):
        for bad in (dict(REPLY,thesis='太短'),dict(REPLY,description='短')):
            self.s.set_meta('stock_research_attempts',{})  # each case is its own day's first attempt
            reply={'status':'ok','text':json.dumps(bad,ensure_ascii=False),'usage':[{}]}
            result=rl.run(self.s,NOW,caller=self.caller(reply))
            self.assertEqual(result[0][1],'error');self.assertIn('invalid_',result[0][2])
            self.assertEqual(self.s.fact('3706','research_detail'),{})

    def test_a_bad_reply_leaves_one_retry_then_stops_for_the_day(self):
        ids=[]
        def failing(request):
            ids.append(request['event_id']);return {'status':'failed','category':'http_client'}
        for _ in range(3):rl.run(self.s,NOW,caller=failing)
        self.assertEqual(len(ids),rl.ATTEMPTS)
        self.assertEqual(ids,['stock-research:3706:20260929:1','stock-research:3706:20260929:2'])
        self.assertEqual(rl.run(self.s,NOW,caller=failing)[0][1],'skipped')

    def test_extra_list_items_are_dropped_and_too_few_are_rejected(self):
        many={'status':'ok','text':json.dumps(dict(REPLY,risks=['第一項風險','第二項風險','第三項風險','第四項風險','第五項風險']),ensure_ascii=False),'usage':[{}]}
        self.assertEqual(rl.run(self.s,NOW,caller=self.caller(many))[0][1],'researched')
        self.assertEqual(len(self.s.fact('3706','research_detail')['risks']),3)
        self.s.set_meta('stock_research_attempts',{})
        self.s.set_fact('3706','research_detail',{})
        few={'status':'ok','text':json.dumps(dict(REPLY,risks=['只有一項風險']),ensure_ascii=False),'usage':[{}]}
        self.assertIn('invalid_risks',rl.run(self.s,NOW,caller=self.caller(few))[0][2])

    def test_overlong_text_is_trimmed_at_a_sentence_not_rejected(self):
        long_thesis='本益比 14.3 低於一年常態 17.4。'*40
        reply={'status':'ok','text':json.dumps(dict(REPLY,thesis=long_thesis),ensure_ascii=False),'usage':[{}]}
        self.assertEqual(rl.run(self.s,NOW,caller=self.caller(reply))[0][1],'researched')
        stored=self.s.fact('3706','research_detail')['thesis']
        self.assertLessEqual(len(stored),rl.LIMITS['thesis'][1])
        self.assertTrue(stored.endswith('。'))

    def test_schema_uses_only_keywords_the_provider_accepts(self):
        text=json.dumps(rl.SCHEMA)
        for unsupported in ('maxItems','minItems','maxLength','minLength'):
            self.assertNotIn(unsupported,text)

    def test_budget_denial_and_model_failure_are_reported_not_stored(self):
        for reply,expected in (({'status':'denied','reason':'daily_budget'},'denied'),
                               ({'status':'failed','category':'output_truncated'},'failed')):
            result=rl.run(self.s,NOW,caller=self.caller(reply))
            self.assertEqual(result[0][1],expected)
            self.assertEqual(self.s.fact('3706','research_detail'),{})
        self.assertEqual(len(self.s.meta('stock_research_run')['results']),1)

if __name__=='__main__':unittest.main()
