# radar.json v1 前後端契約

所有日期 ISO 8601 含 +08:00；缺資料 JSON null，不使用 0。欄位新增可相容，勿改既有 dashboard.json。

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-10T10:00:00+08:00",
  "mode": "live",
  "market_date": "2026-09-10",
  "coverage": {"universe": 0, "stocks": 0, "etfs": 0, "quotes": 0, "valued": 0},
  "health": [{"name": "試撮", "status": "blocked", "detail": "尚未取得有效試撮", "as_of": null}],
  "items": [{
    "symbol": "0050", "name": "元大台灣50", "kind": "etf_equity", "market": "TSE",
    "industry": "ETF", "description": "追蹤臺灣50指數", "watched": true,
    "quote": {"previous_close": null, "reference_price": null, "price": null, "trial_price": null,
      "as_of": null, "trade_date": null, "is_trial": false, "source": "尚未取得", "source_url": null},
    "valuation": null,
    "signal": {"status": "pending", "status_label": "待估值", "suggested": null,
      "conservative": null, "extreme": null, "reason": "尚無經複核估值", "action": "等待研究",
      "calculated_at": null, "valid_until": null},
    "research": {"thesis": null, "why_now": null, "chips": null, "catalysts": [], "risks": [], "sources": []},
    "valuation_history": []
  }]
}
```

valuation 有值時：id、sweet、add、buy、fair（可選）、avoid（可選）、sell（可選）、reduce（可選）、method、inputs、reason、thesis、as_of、valid_until、sources:[{url,as_of,title}]、evidence_reviewed:true。

valuation_history:[{id,parent_id,status,created_at,applied_at,payload:valuation}]。

signal.status: sweet/add/buy/avoid/pending/blocked/stale。狀態按現價／試撮判定，與建議掛價分離。kind: stock / etf_equity / etf_other；不支援證券僅觀察。所有來源 URL 需為 HTTPS。

前端任何正向訊號超過 valid_until 或當日日期不符，掛價隱藏、摘要改為資料過期。後端失敗必須寫 health；不能把上次成功當新資料。mode=simulation 必須醒目標示，正式 docs/radar.json 不使用測試價格。
