import contextlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from stock_radar import cli
from stock_radar.domain import TW
from stock_radar.store import Store

class QuoteIsolationTests(unittest.TestCase):
    def test_bad_quote_blocks_old_value_but_later_symbols_survive_and_recover(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.db';store=Store(path)
            symbols=['00953B','00945B','6669','3013']
            store.upsert_instruments([{'symbol':s,'kind':'stock'} for s in symbols])
            stamp=datetime.now(TW).isoformat()
            old={'symbol':'00945B','as_of':stamp,'price':10}
            store.observe(old);store.close()
            quotes={s:{'symbol':s,'as_of':stamp,'price':20} for s in symbols}
            quotes['00945B']['as_of']=None
            with patch.object(cli,'fetch_quotes',return_value=quotes),contextlib.redirect_stdout(io.StringIO()):
                cli.main(['--db',str(path),'sync-quotes'])
            store=Store(path)
            self.assertIsNone(store.quote('00945B'))
            self.assertEqual(store.db.execute('SELECT count(*) FROM observations WHERE symbol=?',('00945B',)).fetchone()[0],1)
            for s in ('00953B','6669','3013'):self.assertEqual(store.quote(s)['price'],20)
            meta=store.meta('last_quote_sync');self.assertEqual(meta['received'],3)
            self.assertEqual(meta['rejected'],{'00945B':'SOURCE_TIME_INVALID'})
            store.observe(dict(old,price=21))
            self.assertEqual(store.quote('00945B')['price'],21);store.close()

    def test_missing_and_invalid_payload_are_blocked_not_database_errors(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.db';store=Store(path)
            store.upsert_instruments([{'symbol':s,'kind':'stock'} for s in ('AAA','BBB')]);store.close()
            bad={'BBB':{'symbol':'BBB','as_of':datetime.now(TW).isoformat(),'price':float('nan')}}
            with patch.object(cli,'fetch_quotes',return_value=bad),contextlib.redirect_stdout(io.StringIO()):cli.main(['--db',str(path),'sync-quotes'])
            store=Store(path);self.assertEqual(store.meta('last_quote_sync')['received'],0);store.close()
            valid={'AAA':{'symbol':'AAA','as_of':datetime.now(TW).isoformat(),'price':1}}
            with patch.object(cli,'fetch_quotes',return_value=valid),patch.object(Store,'observe',side_effect=sqlite3.OperationalError('disk full')),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(sqlite3.OperationalError):cli.main(['--db',str(path),'sync-quotes'])

    def test_worker_diagnostic_never_exposes_raw_secret(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('worker',Path(__file__).parents[1]/'tools/stock_service.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        from types import SimpleNamespace
        err=io.StringIO()
        with patch.object(worker.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='TypeError: secret-value',stdout='secret-value')),contextlib.redirect_stderr(err):
            with self.assertRaisesRegex(RuntimeError,'sync-quotes'):worker.run(['python','-m','stock_radar.cli','--db','hidden','sync-quotes'])
        self.assertNotIn('secret-value',err.getvalue());self.assertEqual(json.loads(err.getvalue())['error_type'],'TypeError')
