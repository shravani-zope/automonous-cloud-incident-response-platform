"""Kafka event bus for incident alerts (Redpanda-compatible)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from app.config import settings

logger = logging.getLogger(__name__)

TOPIC_ALERTS = "cloud.incidents.alerts"
TOPIC_UPDATES = "cloud.incidents.updates"

AlertHandler = Callable[[dict], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._producer: Optional[AIOKafkaProducer] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._handler: Optional[AlertHandler] = None
        self._running = False

    async def start(self, handler: AlertHandler) -> None:
        self._handler = handler
        if not settings.kafka_enabled:
            logger.info("Kafka disabled — event bus in no-op mode")
            return
        try:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
            await self._producer.start()
            self._running = True
            self._consumer_task = asyncio.create_task(self._consume_loop())
            logger.info("Kafka event bus started (%s)", settings.kafka_bootstrap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kafka unavailable, continuing without bus: %s", exc)
            self._running = False

    async def stop(self) -> None:
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        if self._producer:
            await self._producer.stop()

    async def publish_alert(self, payload: dict) -> None:
        if not self._producer:
            return
        await self._producer.send_and_wait(TOPIC_ALERTS, payload)

    async def publish_update(self, payload: dict) -> None:
        if not self._producer:
            return
        await self._producer.send_and_wait(TOPIC_UPDATES, payload)

    async def _consume_loop(self) -> None:
        consumer = AIOKafkaConsumer(
            TOPIC_ALERTS,
            bootstrap_servers=settings.kafka_bootstrap,
            group_id="acirp-responders",
            auto_offset_reset="latest",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        try:
            await consumer.start()
            # Ensure topic exists by waiting briefly
            await asyncio.sleep(1)
            async for msg in consumer:
                if not self._running:
                    break
                if self._handler:
                    try:
                        await self._handler(msg.value)
                    except Exception:  # noqa: BLE001
                        logger.exception("Failed handling alert event")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kafka consumer stopped: %s", exc)
        finally:
            await consumer.stop()


event_bus = EventBus()
