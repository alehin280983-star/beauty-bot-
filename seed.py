"""
Заповнення послуг і майстрів.
Запуск:
    docker compose exec bot python seed.py
"""
import asyncio
import os
import sys

os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
sys.path.insert(0, "/app")

from decimal import Decimal
from db.session import AsyncSessionFactory
from db.models import Master, MasterService, Service
from sqlalchemy import select, delete

# ── Майстри ───────────────────────────────────────────────────────────────────

MASTERS = [
    "Перукар",
    "Майстер манікюру",
]

# ── Послуги: (назва, хвилини, ціна) ──────────────────────────────────────────

HAIR_SERVICES = [
    ("Чоловіча стрижка", 30, 350),
    ("Стрижка бороди", 30, 250),
    ("Жіноча стрижка", 60, 550),
    ("Стрижка кінчиків", 30, 400),
    ("Стрижка дитяча", 30, 300),
    ("Фарбування волосся один тон", 120, 850),
    ("Фарбування коріння", 120, 800),
    ("Живлення та зволоження волосся", 60, 700),
    ("Вкладання волосся", 60, 500),
]

NAIL_SERVICES = [
    ("Комплексний манікюр", 90, 750),
    ("Комплексний педикюр", 90, 850),
    ("Нарощування нігтів", 120, 1100),
    ("Корекція нарощенних нігтів", 90, 700),
    ("Покриття гель-лак", 30, 350),
    ("Зняття гель-лаку + форма нігтів", 60, 200),
    ("Манікюр дитячий", 30, 200),
    ("Манікюр", 60, 425),
    ("Педикюр", 60, 500),
    ("Педикюр частковий", 30, 300),
]

# Які послуги до якого майстра
MASTER_SERVICES: dict[str, list[tuple]] = {
    "Перукар": HAIR_SERVICES,
    "Майстер манікюру": NAIL_SERVICES,
}

# ── Скрипт ────────────────────────────────────────────────────────────────────

async def main() -> None:
    async with AsyncSessionFactory() as session:
        # Приховати всі поточні послуги (зберігаємо для старих бронювань)
        print("Приховання старих послуг...")
        from sqlalchemy import update
        await session.execute(update(Service).values(is_visible=False))
        await session.execute(delete(MasterService))
        await session.commit()

        # Додати/оновити майстрів
        master_map: dict[str, Master] = {}
        for name in MASTERS:
            existing = await session.execute(select(Master).where(Master.name == name))
            m = existing.scalar_one_or_none()
            if m is None:
                m = Master(name=name)
                session.add(m)
                await session.flush()
                print(f"  Майстер додано: {name}")
            else:
                print(f"  Майстер вже є:  {name}")
            master_map[name] = m

        # Додати послуги і прив'язати до майстрів
        for master_name, services in MASTER_SERVICES.items():
            master = master_map[master_name]
            print(f"\n{master_name}:")
            for svc_name, duration, price in services:
                s = Service(name=svc_name, duration_min=duration, price=Decimal(str(price)))
                session.add(s)
                await session.flush()
                session.add(MasterService(master_id=master.id, service_id=s.id))
                print(f"  {svc_name} — {duration} хв — {price} грн")

        await session.commit()
        print("\n✅ Готово!")


asyncio.run(main())
