from app.config.settings import get_settings
from app.exchange.okx.public_ws import OkxPublicWebSocket
from app.market.realtime import RealtimeMarketHub

settings = get_settings()
realtime_hub = RealtimeMarketHub(paper_auto_ticks=settings.paper_auto_ticks)
realtime_client = OkxPublicWebSocket(settings, realtime_hub.apply)
