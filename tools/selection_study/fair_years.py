import numpy as np
src = open(__file__.replace('fair_years.py', 'fair.py')).read().split("print(f\"訊號 {days[span[0]]}")[0]
__file__ = __file__.replace("fair_years.py", "fair.py"); exec(src)
CASES = [('趨勢回檔+法人買', 'bblo', '碰布林上緣'), ('趨勢回檔+法人買', 'bblo', '漲回下跌前價位'),
         ('趨勢回檔+法人買', 'bblo', '抱20天(對照)'), ('趨勢回檔', 'bblo', '碰布林上緣'),
         ('低股價淨值比', 'bblo', '碰布林上緣'), ('超跌+法人買', None, '抱20天(對照)'),
         ('隨機', 'bblo', '碰布林上緣')]
for name, mode, ex in CASES:
    by = {}
    for t, j in picks[name]:
        r = trade(t, j, EXITS[ex], mode)
        if r: by.setdefault(days[t][:4], []).append(r[0])
    allr = np.concatenate([np.array(v) for v in by.values()])
    line = '  '.join(f"{y[2:]}:{np.mean(v)*100:+.2f}({np.mean(np.array(v)>0)*100:.0f}%,{len(v)})" for y, v in sorted(by.items()))
    print(f"{name}｜{'掛布林下緣' if mode else '隔天開盤'}｜{ex}：全部 {allr.mean()*100:+.2f}%  ／  {line}")
