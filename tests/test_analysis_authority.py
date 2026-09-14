import unittest,tempfile
from pathlib import Path
from datetime import datetime,timedelta
from stock_radar.store import Store
from stock_radar.domain import TW
class AnalysisAuthorityTests(unittest.TestCase):
 def test_requires_authority_review_and_rejects_old_batch(self):
  now=datetime(2026,9,14,12,tzinfo=TW)
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'test.db');s.upsert_instruments([{'symbol':'TEST','kind':'stock'}])
   v={'id':'A-new','sweet':70,'add':80,'buy':90,'method':'forward_pe','thesis':'fixture','reason':'fixture','as_of':now.isoformat(),'valid_until':(now+timedelta(days=30)).isoformat(),'evidence_reviewed':True,'sources':[{'url':'https://example.com/report','as_of':now.isoformat()}]}
   pid=s.propose('TEST',v,now)
   with self.assertRaises(PermissionError):s.publish_reviewed_analysis(pid,now=now)
   s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
   with self.assertRaises(ValueError):s.publish_reviewed_analysis(pid,now=now)
   v['analysis_review']={k:True for k in ('financials','outlook','valuation_basis','price_structure','risks')}
   v['id']='V20260914-TEST-v1';old=s.propose('TEST',v,now)
   with self.assertRaises(ValueError):s.publish_reviewed_analysis(old,now=now)
   v['id']='A-new-v2';pid=s.propose('TEST',v,now);s.publish_reviewed_analysis(pid,now=now)
   self.assertEqual(s.active('TEST')['id'],pid)
   self.assertEqual(next(x for x in s.versions('TEST') if x['id']==pid)['applied_at'],now.isoformat())
   self.assertEqual(s.db.execute("SELECT applied_by FROM valuations WHERE id=?",(pid,)).fetchone()[0],'codex-research')
   s.close()
