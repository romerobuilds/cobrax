# app/workers/rate_limiter.py
import os
import time
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

def throttle_company(company_id: str, rate_per_min: int, spin_seconds: float = 10.0) -> bool:
    """
    Throttle por empresa: garante ~rate_per_min envios/minuto.
    Implementação simples: "next_allowed_ts" por company.
    Retorna True quando pode enviar, False se não conseguiu dentro do spin_seconds.
    """
    if rate_per_min <= 0:
        return True

    interval = 60.0 / rate_per_min
    key = f"cobrax:rate:{company_id}"

    deadline = time.time() + spin_seconds
    while time.time() < deadline:
        now = time.time()

        # valor atual
        current = r.get(key)
        if current is None:
            # se não existe, tenta setar "agora" como próximo
            if r.set(key, str(now + interval), nx=True, ex=int(interval) + 5):
                return True
            continue

        next_allowed = float(current)
        if now >= next_allowed:
            # tenta avançar o relógio de forma atômica via WATCH/MULTI
            pipe = r.pipeline()
            try:
                pipe.watch(key)
                cur2 = pipe.get(key)
                if cur2 is None:
                    pipe.unwatch()
                    continue

                next2 = float(cur2)
                if time.time() < next2:
                    pipe.unwatch()
                    time.sleep(0.2)
                    continue

                new_next = time.time() + interval
                pipe.multi()
                pipe.set(key, str(new_next), ex=int(interval) + 5)
                pipe.execute()
                return True
            except redis.WatchError:
                # alguém atualizou junto, tenta de novo
                continue
            finally:
                try:
                    pipe.reset()
                except Exception:
                    pass
        else:
            # espera um pouquinho e tenta de novo
            time.sleep(min(0.25, max(0.05, next_allowed - now)))

    return False
