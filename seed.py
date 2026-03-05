"""
Быстрое заполнение услуг и мастеров.
Отредактируй MASTERS и SERVICES, затем запусти:
    docker compose exec bot python seed.py
"""
import asyncio
from decimal import Decimal

# ── Настрой здесь ─────────────────────────────────────────────────────────────

MASTERS = [
    "Валентина",
    "Ана мастер маникюру",
]

SERVICES = [
    # (название, длительность мин, цена)
    ("Стрижка женская", 60, 500),
    ("Стрижка мужская", 30, 300),
    ("Окрашивание", 120, 1500),
    ("Маникюр", 60, 600),
    ("Педикюр", 90, 800),
    ("Ламинирование волос", 90, 2000),
    ("Укладка", 45, 400),
    ("Мелирование", 150, 2500),
]

# Какие услуги привязать к каждому мастеру (по имени из MASTERS)
# None = все услуги
MASTER_SERVICES: dict[str, list[str] | None] = {
    "Валентина": None,              # all services
    "Ана мастер маникюру": None,    # all services
}

# ── Скрипт (не редактируй) ────────────────────────────────────────────────────

import os, sys
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
sys.path.insert(0, "/app")

from config import settings
from db.session import AsyncSessionFactory
from db.models import Master, MasterService, Service
from sqlalchemy import select


async def main() -> None:
    async with AsyncSessionFactory() as session:
        # Add masters
        master_map: dict[str, Master] = {}
        for name in MASTERS:
            existing = await session.execute(select(Master).where(Master.name == name))
            m = existing.scalar_one_or_none()
            if m is None:
                m = Master(name=name)
                session.add(m)
                await session.flush()
                print(f"  Мастер добавлен: {name}")
            else:
                print(f"  Мастер уже есть: {name}")
            master_map[name] = m

        # Add services
        service_map: dict[str, Service] = {}
        for svc_name, duration, price in SERVICES:
            existing = await session.execute(select(Service).where(Service.name == svc_name))
            s = existing.scalar_one_or_none()
            if s is None:
                s = Service(name=svc_name, duration_min=duration, price=Decimal(str(price)))
                session.add(s)
                await session.flush()
                print(f"  Услуга добавлена: {svc_name}")
            else:
                print(f"  Услуга уже есть: {svc_name}")
            service_map[svc_name] = s

        # Link masters ↔ services
        for master_name, svc_names in MASTER_SERVICES.items():
            master = master_map[master_name]
            services_to_link = (
                list(service_map.values())
                if svc_names is None
                else [service_map[n] for n in svc_names]
            )
            for svc in services_to_link:
                existing = await session.execute(
                    select(MasterService)
                    .where(MasterService.master_id == master.id)
                    .where(MasterService.service_id == svc.id)
                )
                if existing.scalar_one_or_none() is None:
                    session.add(MasterService(master_id=master.id, service_id=svc.id))

            print(f"  {master_name} → {len(services_to_link)} услуг привязано")

        await session.commit()
        print("\n✅ Готово!")


asyncio.run(main())
