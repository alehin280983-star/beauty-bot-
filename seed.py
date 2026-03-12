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
    # Вкладання волосся
    ("Вкладання волосся", 60, 500),
    # Чоловіча стрижка
    ("Чоловіча стрижка", 30, 350),
    ("Стрижка бороди", 30, 250),
    # Жіноча стрижка
    ("Жіноча стрижка", 60, 550),
    ("Стрижка кінчиків", 30, 400),
    ("Дитяча стрижка (до 7 років)", 30, 300),
    # Фарбування
    ("Фарбування (зі своєю фарбою)", 120, 850),
    ("Фарбування коріння", 60, 800),
    ("Мелірування", 120, 1000),
    ("Шатуш / омбре", 120, 1200),
    ("Кератинове вирівнювання", 120, 1800),
    # Догляд
    ("Догляд за волоссям", 60, 700),
    ("Реконструкція волосся", 60, 1200),
]

NAIL_SERVICES = [
    # Манікюр
    ("Манікюр", 60, 425),
    ("Манікюр комплекс (зняття + покриття)", 90, 750),
    ("Манікюр дитячий", 30, 200),
    # Педикюр
    ("Педикюр", 60, 500),
    ("Педикюр комплекс (зняття + покриття)", 90, 850),
    # Покриття
    ("Покриття гель-лак", 30, 350),
    ("Покриття «Френч»", 30, 450),
    ("Зняття гель-лаку", 30, 100),
    # Нарощування
    ("Нарощування нігтів", 120, 1100),
    # Брови / вії
    ("Корекція брів", 30, 250),
    ("Фарбування брів", 30, 250),
    ("Корекція + фарбування брів", 30, 450),
]

# Які послуги до якого майстра
MASTER_SERVICES: dict[str, list[tuple]] = {
    "Перукар": HAIR_SERVICES,
    "Майстер манікюру": NAIL_SERVICES,
}

# ── Скрипт ────────────────────────────────────────────────────────────────────

async def main() -> None:
    async with AsyncSessionFactory() as session:
        # Очистити старі прив'язки та послуги
        print("Очищення старих даних...")
        await session.execute(delete(MasterService))
        await session.execute(delete(Service))
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
