from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.market import Candle
from app.structure import analyze_structure, detect_fvg


def row(i, o, h, l, c):
    return Candle(timestamp=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(minutes=5*i),open=Decimal(o),high=Decimal(h),low=Decimal(l),close=Decimal(c),volume_contracts=Decimal(100),volume_currency=Decimal(100),volume_quote=Decimal(10000),confirmed=True)


def test_bullish_fvg_detection():
    rows=[row(0,'100','101','99','100'),row(1,'102','103','102','102.5'),row(2,'104','105','103','104')]
    gaps=detect_fvg(rows)
    assert gaps and gaps[0].direction == 'bullish'


def test_structure_returns_stable_shape():
    rows=[]
    for i in range(50):
        value=Decimal(100+i)
        rows.append(row(i,str(value),str(value+2),str(value-2),str(value+1)))
    result=analyze_structure(rows,Decimal('140'),Decimal('130'),Decimal('120'))
    assert result.trend == 'strong_bullish'


def test_detect_order_blocks_returns_list() -> None:
    from app.structure.engine import detect_order_blocks
    rows=[row(i, str(100+i), str(102+i), str(98+i), str(101+i)) for i in range(12)]
    assert isinstance(detect_order_blocks(rows), list)
