from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config.settings import get_settings

settings = get_settings()


async def check_redis() -> tuple[bool, str]:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        result = await client.ping()
        return bool(result), "redis reachable" if result else "redis ping returned false"
    except RedisError as exc:
        return False, f"redis unavailable: {exc.__class__.__name__}"
    except Exception as exc:
        return False, f"redis unavailable: {exc.__class__.__name__}"
    finally:
        await client.aclose()
